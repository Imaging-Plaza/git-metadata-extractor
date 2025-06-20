from gimie.project import Project
import json

def extract_gimie(full_path: str, format: str = "json-ld", serialize: bool = False):
    """
    Extracts the GIMIE project from the given path.
    
    Args:
        full_path (str): The full path to the GIMIE project.
        format (str): The format to serialize the graph. Default is 'json-ld', or 'ttl'.
        
    Returns:
        Project: The GIMIE project object.
    """

    proj = Project(full_path)

    # To retrieve the rdflib.Graph object
    g = proj.extract()

    print(g)

    if serialize:
        # To retrieve the serialized graph
        output = g.serialize(format=format)
    else:
        output = json.loads(g.serialize(format=format, encoding="utf-8"))

    if output is None:
        return None
    else:
        return output
    