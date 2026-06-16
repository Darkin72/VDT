import re
from typing import Any

from app import llm_service, logging_service
from app.agents.central import extract_json_object

READ_ONLY_SPARQL_PATTERN = re.compile(
    r"\b(INSERT|DELETE|LOAD|CLEAR|CREATE|DROP|MOVE|COPY|ADD|SERVICE)\b",
    re.IGNORECASE,
)

ONTOLOGY_HINTS = """
Local GraphDB schema hints from Ontology/ontology--DEV_type=parsed_sorted.nt:
- The graph uses DBpedia ontology IRIs. Main namespace: http://dbpedia.org/ontology/ as dbo:.
- Resource namespace is usually http://dbpedia.org/resource/ as dbr:.
- Labels are stored with rdfs:label, often with @en language tags.
- Classes are declared as owl:Class. Examples:
  dbo:Academic, dbo:AcademicConference, dbo:AcademicJournal, dbo:AcademicSubject,
  dbo:Activity, dbo:Actor, dbo:AdministrativeRegion, dbo:Agent, dbo:Aircraft,
  dbo:Airline, dbo:Airport, dbo:Album, dbo:Ambassador, dbo:Animal, dbo:Architect,
  dbo:ArchitecturalStructure, dbo:Artist, dbo:Artwork, dbo:Astronaut, dbo:Athlete,
  dbo:Automobile, dbo:Award, dbo:Band, dbo:Bank, dbo:BaseballPlayer,
  dbo:BasketballPlayer, dbo:BasketballTeam, dbo:Bay, dbo:Beach.
- Object properties are declared as owl:ObjectProperty and connect resources to resources. Examples:
  dbo:academicAdvisor, dbo:academicDiscipline, dbo:academyAward, dbo:achievement,
  dbo:activity, dbo:adjacentSettlement, dbo:administrativeCenter,
  dbo:administrator, dbo:affiliation, dbo:agency, dbo:airline, dbo:album,
  dbo:almaMater, dbo:architect, dbo:architecturalStyle, dbo:artist,
  dbo:author, dbo:award, dbo:bandMember, dbo:basedOn, dbo:basinCountry.
- Some numeric/literal datatype properties are class-scoped IRIs, not normal dbo:localName CURIEs.
  Use full IRIs for these, for example:
  <http://dbpedia.org/ontology/Person/height>, <http://dbpedia.org/ontology/Person/weight>,
  <http://dbpedia.org/ontology/Building/floorArea>,
  <http://dbpedia.org/ontology/Automobile/fuelCapacity>,
  <http://dbpedia.org/ontology/Automobile/wheelbase>,
  <http://dbpedia.org/ontology/Engine/topSpeed>,
  <http://dbpedia.org/ontology/Engine/powerOutput>,
  <http://dbpedia.org/ontology/PopulatedPlace/areaTotal>,
  <http://dbpedia.org/ontology/PopulatedPlace/populationDensity>,
  <http://dbpedia.org/ontology/Lake/volume>.
- Datatype units use http://dbpedia.org/datatype/, for example metre, kilometre, kilogram, kelvin,
  squareKilometre, inhabitantsPerSquareKilometre.
- If an exact property is uncertain, first prefer broad predicate discovery queries using rdfs:label filters
  and return candidate ?p ?pLabel ?value. Do not invent properties outside dbo:, dbp:, rdf:, rdfs:, foaf:.
""".strip()

COMMON_PREFIXES = {
    "dbo": "http://dbpedia.org/ontology/",
    "dbr": "http://dbpedia.org/resource/",
    "dbp": "http://dbpedia.org/property/",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "foaf": "http://xmlns.com/foaf/0.1/",
    "owl": "http://www.w3.org/2002/07/owl#",
}

PREFIX_DECLARATION_PATTERN = re.compile(r"(?im)^\s*PREFIX\s+([A-Za-z][\w-]*):")
PREFIX_USAGE_PATTERN = re.compile(r"(?<![\w:/#])([A-Za-z][\w-]*):[A-Za-z_][\w.-]*")


def add_missing_common_prefixes(query: str) -> str:
    declared_prefixes = set(PREFIX_DECLARATION_PATTERN.findall(query))
    used_prefixes = set(PREFIX_USAGE_PATTERN.findall(query))
    missing_prefixes = [
        prefix for prefix in COMMON_PREFIXES
        if prefix in used_prefixes and prefix not in declared_prefixes
    ]
    if not missing_prefixes:
        return query

    declarations = "\n".join(
        f"PREFIX {prefix}: <{COMMON_PREFIXES[prefix]}>" for prefix in missing_prefixes
    )
    return f"{declarations}\n{query}"


def is_read_only_sparql(query: str) -> bool:
    if not query or READ_ONLY_SPARQL_PATTERN.search(query):
        return False
    without_prefixes = re.sub(r"(?im)^\s*PREFIX\s+[^\n]+\n?", "", query).strip()
    return without_prefixes.upper().startswith(("SELECT", "ASK"))


def generate_sparql(user_prompt: str, query_description: str) -> str:
    prompt = (
        "You are a SPARQL coder for the local GraphDB.\n"
        "Create one read-only SPARQL query from the central agent description.\n"
        "Only create SELECT or ASK. Do not use INSERT, DELETE, UPDATE, or SERVICE.\n"
        "Prefer returning neutral factual evidence: entities, relationships, labels, dates, counts, and literal values needed by the core question.\n"
        "When selecting resources, include rdfs:label values when available and prefer FILTER(lang(?label) = 'en').\n"
        "For entity lookup, use exact dbr:Entity_Name only when confident; otherwise search labels with CONTAINS(LCASE(STR(?label)), \"text\").\n"
        "Do not include answer choices, option IDs, VALUES blocks for choices, or BINDs mapping choices to options. The central agent handles choices later.\n"
        "SPARQL function syntax matters: use CONTAINS(LCASE(STR(?label)), \"text\"), never LCASE(STR(?label)) CONTAINS(\"text\").\n\n"
        "Common prefixes:\n"
        "PREFIX dbo: <http://dbpedia.org/ontology/>\n"
        "PREFIX dbr: <http://dbpedia.org/resource/>\n"
        "PREFIX dbp: <http://dbpedia.org/property/>\n"
        "PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>\n"
        "PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>\n"
        "PREFIX foaf: <http://xmlns.com/foaf/0.1/>\n"
        "PREFIX owl: <http://www.w3.org/2002/07/owl#>\n\n"
        f"Schema hints:\n{ONTOLOGY_HINTS}\n\n"
        "Return only valid JSON with this schema: {\"sparql\":\"...\"}.\n"
        "If a useful query cannot be created, return {\"sparql\":\"\"}.\n\n"
        f"Original user prompt, for context only; do not extract answer options from it:\n{user_prompt}\n\n"
        f"Central agent query description:\n{query_description}"
    )
    raw_text = llm_service.complete_text(
        [
            llm_service.system_message("You are a SPARQL coder. Return only SPARQL JSON."),
            llm_service.user_message(prompt),
        ]
    )
    logging_service.verbose_text("sparql_agent.raw_response", raw_text)
    data: dict[str, Any] = extract_json_object(raw_text)
    sparql = str(data.get("sparql", "")).strip() if data else ""
    sparql = add_missing_common_prefixes(sparql)
    if not is_read_only_sparql(sparql):
        logging_service.agent_step("sparql_agent.rejected_sparql", {"sparql": sparql})
        return ""
    logging_service.agent_text("sparql_agent.final_sparql", sparql)
    return sparql
