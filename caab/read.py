#  Copyright (c) 2021.  Atlas of Living Australia
#   All Rights Reserved.
#
#   The contents of this file are subject to the Mozilla Public
#   License Version 1.1 (the "License"); you may not use this file
#   except in compliance with the License. You may obtain a copy of
#   the License at http://www.mozilla.org/MPL/
#
#   Software distributed under the License is distributed on an "AS  IS" basis,
#   WITHOUT WARRANTY OF ANY KIND, either express or
#   implied. See the License for the specific language governing
#   rights and limitations under the License.
import dwc.transform
from ala.transform import PublisherSource, CollectorySource
from caab.schema import CaabSchema
from caab.todwc import CaabToDwcTaxonTaxonTransform, CaabToDwcTaxonSynonymTransform, CaabToDwcVernacularTransform
from dwc.meta import MetaFile, EmlFile
from dwc.schema import NomenclaturalCodeMapSchema, NameMapSchema, ScientificNameStatusSchema
from dwc.transform import DwcRename, DwcTaxonValidate, DwcScientificNameStatus
from processing.dataset import Record
from processing.orchestrate import Orchestrator
from processing.sink import CsvSink
from processing.source import ExcelSource, CsvSource
from processing.transform import FilterTransform, MapTransform, strip_markup, normalise_spaces, DenormaliseTransform, \
    MergeTransform, LookupTransform


def is_current_taxon(record: Record):
    spcode = str(record.SPCODE)
    return not record.NON_CURRENT_FLAG and not spcode.startswith("99") and not spcode.startswith("8") and (record.SCIENTIFIC_NAME is not None or record.DISPLAY_NAME is not None)

def is_usable_taxon(record: Record):
    name: str = record.scientificName
    if name is None or len(name) < 2:
        return False
    return dwc.transform.SCIENTIFIC_START.match(name)

def clean_scientific(s: str):
    s = strip_markup(s)
    if s is None:
        return None
    if s.startswith('"'):
        s = s.replace('"', ' ')
    s = normalise_spaces(s)
    return s

def clean_common(s: str):
    if s is None or len(s) == 0:
        return None
    common = []
    for ss in s.split('|'):
        ss = strip_markup(ss)
        if ss is None:
            continue
        if ss.startswith('[') and ss.endswith(']'):
            ss = ss[1:-1].strip()
        if ss.startswith('a '):
            ss = ss[2:]
        if ss.startswith('an '):
            ss = ss[3:]
        ss = normalise_spaces(ss)
        common.append(ss)
    return '|'.join(common)

def clean_rank(s: str):
    if s is None:
        return None
    s = s.strip().lower()
    if len(s) == 0:
        return None
    return s

def reader() -> Orchestrator:
    taxon_file = "caab_dump.csv"
    nomenclatural_code_file = "Nomenclatural_Code_Map.csv"
    scientific_name_status_file = "Scientific_Name_Patterns.csv"
    caab_schema = CaabSchema()
    caab_nomenclatural_code_schema = NomenclaturalCodeMapSchema()
    scientific_name_status_schema = ScientificNameStatusSchema()

    nomenclatural_code_map = CsvSource.create("nomenclatural_code_map", nomenclatural_code_file, "ala", caab_nomenclatural_code_schema)
    name_patterns = CsvSource.create("name_patterns", scientific_name_status_file, 'excel', scientific_name_status_schema)
    taxon_source = CsvSource.create("taxon_source", taxon_file, "excel", caab_schema, encoding='utf-8-sig', no_errors=True)
    taxon_current = FilterTransform.create("taxon_current", taxon_source.output, is_current_taxon)
    taxon_clean = MapTransform.create("taxon_clean",  taxon_current.output, caab_schema, {
        'SCIENTIFIC_NAME': (lambda r: clean_scientific(r.SCIENTIFIC_NAME)),
        'AUTHORITY': (lambda r: strip_markup(r.AUTHORITY)),
        'DISPLAY_NAME': (lambda r: clean_scientific(r.DISPLAY_NAME)),
        'COMMON_NAME': (lambda r: clean_common(r.COMMON_NAME)),
        'COMMON_NAMES_LIST': (lambda r: clean_common(r.COMMON_NAMES_LIST)),
        'FAMILY': (lambda r: clean_scientific(r.FAMILY)),
        'KINGDOM': (lambda r: clean_scientific(r.KINGDOM)),
        'PHYLUM': (lambda r: clean_scientific(r.PHYLUM)),
        'SUBPHYLUM': (lambda r: clean_scientific(r.SUBPHYLUM)),
        'CLASS': (lambda r: clean_scientific(r.CLASS)),
        'SUBCLASS': (lambda r: clean_scientific(r.SUBCLASS)),
        'ORDER_NAME': (lambda r: clean_scientific(r.ORDER_NAME)),
        'SUBORDER': (lambda r: clean_scientific(r.SUBORDER)),
        'INFRAORDER': (lambda r: clean_scientific(r.INFRAORDER)),
        'GENUS': (lambda r: clean_scientific(r.GENUS)),
        'SPECIES': (lambda r: clean_scientific(r.SPECIES)),
        'SUBSPECIES': (lambda r: clean_scientific(r.SUBSPECIES)),
        'SUBGENUS': (lambda r: clean_scientific(r.SUBGENUS)),
        'VARIETY': (lambda r: clean_scientific(r.VARIETY)),
        'RANK': (lambda r: clean_rank(r.RANK))
    }, auto=True)
    taxon_coded = LookupTransform.create('taxon_coded', taxon_clean.output, nomenclatural_code_map.output, 'KINGDOM', 'kingdom', record_unmatched=True)
    synonyms = DenormaliseTransform.delimiter("synonyms", taxon_coded.output, 'RECENT_SYNONYMS', '|', include_empty=False)
    vernacular = DenormaliseTransform.delimiter("vernacular", taxon_coded.output, 'COMMON_NAMES_LIST', '|', include_empty=False)
    dwc_accepted = CaabToDwcTaxonTaxonTransform.create("dwc_accepted", taxon_coded.output, taxon_clean.output, 'SPCODE', 'PARENT_ID', taxonomicStatus='accepted', allow_unmatched=True)
    dwc_synonym = CaabToDwcTaxonSynonymTransform.create("dwc_synonym", synonyms.output, taxonomicStatus='synonym')
    dwc_synonym_status = DwcScientificNameStatus.create('dwc_synonym_status', dwc_synonym.output, name_patterns.output)
    dwc_taxon = MergeTransform.create("dwc_taxon", dwc_accepted.output, dwc_synonym_status.output)
    name_map = CsvSource.create('name_map', 'Name_Map.csv', 'ala', NameMapSchema())
    dwc_renamed = DwcRename.create('rename', dwc_taxon.output, name_map.output)
    dwc_usable = FilterTransform.create("dwc_usable", dwc_renamed.output, is_usable_taxon)
    dwc_validated = DwcTaxonValidate.create("dwc_validated", dwc_usable.output, check_names=True, no_errors=True)
    dwc_vernacular_standard = CaabToDwcVernacularTransform.create("dwc_vernacular_standard", taxon_coded.output, status='standard', isPreferredName=True)
    dwc_vernacular_common = CaabToDwcVernacularTransform.create("dwc_vernacular_common", vernacular.output, status='common', isPreferredName=False)
    dwc_vernacular = MergeTransform.create("dwc_vernacular", dwc_vernacular_standard.output, dwc_vernacular_common.output)
    taxon_output = CsvSink("dwc_taxon_output", dwc_validated.output, "taxon.csv", "excel", reduce=True)
    vernacular_output = CsvSink.create("dwc_vernacular_output", dwc_vernacular.output, "vernacularNames.csv", "excel", reduce=True)
    dwc_meta = MetaFile.create('dwc_meta', taxon_output, vernacular_output)
    publisher = PublisherSource.create('publisher')
    metadata = CollectorySource.create('metadata')
    dwc_eml = EmlFile.create('dwc_eml', metadata.output, publisher.output)



    orchestrator = Orchestrator("caab",
                                [
                                    nomenclatural_code_map,
                                    name_patterns,
                                    taxon_source,
                                    taxon_current,
                                    taxon_clean,
                                    taxon_coded,
                                    synonyms,
                                    vernacular,
                                    dwc_accepted,
                                    dwc_synonym,
                                    dwc_synonym_status,
                                    dwc_taxon,
                                    name_map,
                                    dwc_renamed,
                                    dwc_usable,
                                    dwc_validated,
                                    taxon_output,
                                    dwc_vernacular_standard,
                                    dwc_vernacular_common,
                                    dwc_vernacular,
                                    vernacular_output,
                                    dwc_meta,
                                    metadata,
                                    publisher,
                                    dwc_eml
                                ])
    return orchestrator
