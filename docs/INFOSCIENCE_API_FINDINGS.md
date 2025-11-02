# Infoscience API Investigation Findings

## Date: 2025-11-02

## Summary
Investigation of EPFL's Infoscience API (DSpace 7.6) to understand endpoint behavior and fix 403/404 errors.

## Key Findings

### 1. General Search (WITHOUT dsoType)
- **Endpoint**: `/discover/search/objects`
- **Status**: ✅ WORKS
- **Query**: `?query=Mathis Lab&size=3`
- **Results**: 95 results found
- **Returns**: Mixed types (items, etc.)

### 2. Search with dsoType=item
- **Endpoint**: `/discover/search/objects?dsoType=item`
- **Status**: ✅ WORKS  
- **Query**: `?query=DeepLabCut&size=3&dsoType=item`
- **Results**: 273 results found
- **Use for**: Publications, items

### 3. Search with dsoType=community
- **Endpoint**: `/discover/search/objects?dsoType=community`
- **Status**: ⚠️ RETURNS EMPTY (not 403)
- **Results**: 0 results
- **Conclusion**: EPFL may not use DSpace communities or they're not searchable

### 4. Direct UUID Access
- **Endpoint**: `/core/items/{uuid}`
- **Status**: ✅ WORKS
- **Example**: `/core/items/492614b1-7dc9-4d24-81f7-648f1223de71`
- **Returns**: Full item metadata
- **Use for**: Direct access to publications, persons, orgunits by UUID

### 5. Search Publications
- **Function**: `search_publications()`
- **Status**: ✅ WORKS
- **Uses**: `/discover/search/objects` with `configuration=researchoutputs`
- **Results**: 273 results for "DeepLabCut"

### 6. Search Authors  
- **Function**: `search_authors()`
- **Status**: ✅ FIXED
- **Endpoint Tried**: `/eperson/profiles/search/byName` (404 Not Found - doesn't exist)
- **Solution**: Use `configuration=person` like the web UI
- **Working Endpoint**: `/discover/search/objects?query=alexander%20mathis&configuration=person`
- **Web UI**: https://infoscience.epfl.ch/search?page=1&configuration=person&query=alexander%20mathis
- **Results**: Successfully returns person profiles with full metadata
- **Fallback**: Search by author name in publications (dc.contributor.author field) if no person profiles found

### 7. Search Labs
- **Function**: `search_labs()`
- **Status**: ✅ FIXED
- **Endpoint Tried**: `/discover/search/objects?dsoType=community` (returns 0 results)
- **Solution**: Use `configuration=orgunit` like the web UI for organizational units
- **Working Endpoint**: `/discover/search/objects?query=mathis+lab&configuration=orgunit`
- **Results**: Successfully returns organizational unit profiles
- **Fallback**: Search publications and extract lab info from metadata (dc.contributor.lab, dc.contributor.unit, etc.)

## Entity Endpoints from User URLs

The user provided these working entity URLs:
- **Orgunits**: `https://infoscience.epfl.ch/entities/orgunit/{uuid}`
- **Persons**: `https://infoscience.epfl.ch/entities/person/{uuid}`
- **Publications**: `https://infoscience.epfl.ch/entities/publication/{uuid}`

These suggest the existence of `/entities/` API endpoints that we should investigate and potentially use.

## Implementation Status

### ✅ 1. Author Search - FIXED
- Removed the broken `/eperson/profiles/search/byName` endpoint reference
- Now uses `configuration=person` (primary method)
- Falls back to publication author search if needed
- Successfully finds profiles like "Alexander Mathis" and "Mackenzie Weygandt Mathis"

### ✅ 2. Lab Search - FIXED
- Removed `dsoType=community` approach (was returning empty)
- Now uses `configuration=orgunit` (primary method)
- Falls back to searching publications and extracting lab affiliations
- Successfully finds organizational units like "Mathis Lab"

### ✅ 3. Direct Entity Access - IMPLEMENTED
Created `get_entity_by_uuid()` function:
```python
async def get_entity_by_uuid(uuid: str, entity_type: Optional[str] = None):
    """
    Get entity directly by UUID using /core/items/{uuid}
    
    Args:
        uuid: Entity UUID
        entity_type: Optional hint ("publication", "person", "orgunit")
        
    Returns:
        Entity data parsed based on type
    """
    # Uses /core/items/{uuid} which works for all entity types
```

### ✅ 4. Entity Type Detection - IMPLEMENTED
- Parser functions detect entity type from metadata
- `_parse_publication()`, `_parse_author()`, `_parse_lab()` handle different types
- Automatic type detection based on metadata structure

## Configuration Parameter

The `configuration` parameter works and maps to the web UI search configurations:
- `configuration=researchoutputs` - for publications ✅ TESTED
- `configuration=person` - for person profiles ✅ TESTED (like web UI person search)
- `configuration=orgunit` - for organizational units ✅ TESTED (labs, departments, etc.)

## Conclusion

### ✅ All Issues Fixed!

Original problems:
1. **Author search endpoint doesn't exist** - ✅ FIXED: Use `configuration=person`
2. **dsoType=community/collection returns empty** - ✅ FIXED: Use `configuration=orgunit`  
3. **Direct entity access** - ✅ IMPLEMENTED: `get_entity_by_uuid()` function added

The API now works excellently for:
- ✅ Publication search (very effective) - `configuration=researchoutputs`
- ✅ Person/author search - `configuration=person`
- ✅ Organizational unit/lab search - `configuration=orgunit`
- ✅ Direct UUID-based item retrieval - `/core/items/{uuid}`
- ✅ General keyword search

### Key Insight

The key was understanding that Infoscience uses **configuration-based search** (like the web UI) rather than the traditional DSpace dsoType filtering:
- **Web UI**: Uses `?configuration=person` query parameter
- **API**: Same parameter works in `/discover/search/objects` endpoint
- **Configurations available**: `researchoutputs`, `person`, `orgunit`

This matches how the web UI works and provides direct access to typed entity searches!

