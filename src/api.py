from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
import os
from .core.gimie_methods import extract_gimie
from .core.models import convert_jsonld_to_pydantic, convert_pydantic_to_zod_form_dict
from .core.genai_model import llm_request_repo_infos, llm_request_userorg_infos
from .core.users_parser import parse_github_user
from .core.orgs_parser import parse_github_organization
from .utils.utils import merge_jsonld



app = FastAPI()

@app.get("/")
def index():
    return {"title": "Hello, welcome to the Git Metadata Extractor v0.1.0. Gimie Version 0.7.2. "}

@app.get("/v1/extract/json/{full_path:path}")
async def extract(full_path:str):

    jsonld_gimie_data = extract_gimie(full_path, format="json-ld")

    print("jsonld_gimie_data", jsonld_gimie_data)

    try:
        llm_result = llm_request_repo_infos(str(full_path))
    except Exception as e:
        raise HTTPException(
            status_code=424, 
            detail=f"Error from LLM service: {e}"
        )

    merged_results = merge_jsonld(jsonld_gimie_data, llm_result)

    pydantic_data = convert_jsonld_to_pydantic(merged_results["@graph"])

    zod_data = convert_pydantic_to_zod_form_dict(pydantic_data)

    return {"link": full_path, 
            "output": zod_data}

@app.get("/v1/extract/json-ld/{full_path:path}")
async def extract(full_path:str):

    jsonld_gimie_data = extract_gimie(full_path, format="json-ld")

    try:
        llm_result = llm_request_repo_infos(str(full_path))
    except Exception as e:
        raise HTTPException(
            status_code=424, 
            detail=f"Error from LLM service: {e}"
        )

    merged_results = merge_jsonld(jsonld_gimie_data, llm_result)

    return {"link": full_path, 
            "output": merged_results}

@app.get("/v1/org/json/{full_path:path}")
async def get_org_json(full_path: str):

    try:
        org_metadata = parse_github_organization(full_path.split("/")[-1])

        parsed_org_metadata = llm_request_userorg_infos(org_metadata, item_type="org")

        org_metadata_dict = org_metadata.model_dump()
        org_metadata_dict.update(parsed_org_metadata)

    except Exception as e:
        raise HTTPException(
            status_code=424, 
            detail=f"Error from Organization JSON service: {e}"
        )

    return {"link": full_path, 
            "output": org_metadata_dict}

@app.get("/v1/user/json/{full_path:path}")
async def get_user_json(full_path: str):

    try:
        user_metadata = parse_github_user(full_path.split("/")[-1])

        parsed_user_metadata = llm_request_userorg_infos(user_metadata, item_type="user")

        user_metadata_dict = user_metadata.model_dump()

        user_metadata_dict.update(parsed_user_metadata)

    except Exception as e:
        raise HTTPException(
            status_code=424, 
            detail=f"Error from Get User service: {e}"
        )

    return {"link": full_path, 
            "output": user_metadata_dict}
    
@app.get("/v1/gimie/{full_path:path}")
async def gimie(full_path:str, 
                format:str = "json-ld"):
    try:
        gimie_output = extract_gimie(full_path, format=format)
    except Exception as e:
        raise HTTPException(
            status_code=424, #?
            detail=f"Error from Gimie service: {e}"
        )
    
    return {"link": full_path, 
            "output": gimie_output}

@app.get("/v1/llm/json-ld/{full_path:path}")
async def llm(full_path:str):

    try:
        llm_result = llm_request_repo_infos(str(full_path))
    except Exception as e:
        raise HTTPException(
            status_code=424, 
            detail=f"Error from LLM service: {e}"
        )
    
    return {"link": full_path, 
            "output": llm_result}

@app.get("/v1/llm/json/{full_path:path}")
async def llm(full_path:str):

    try:
        llm_result = llm_request_repo_infos(str(full_path), output_format="json")
    except Exception as e:
        raise HTTPException(
            status_code=424, 
            detail=f"Error from LLM service: {e}"
        )
    
    return {"link": full_path, 
            "output": llm_result}

@app.exception_handler(ValueError)
async def value_error_exception_handler(request: Request, exc: ValueError):
    return JSONResponse(
        status_code=400,
        content={"message": str(exc)},
    )