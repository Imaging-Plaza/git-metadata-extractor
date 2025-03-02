from langchain.prompts import ChatPromptTemplate


question_is_imaging_software = """I need you to analyse this GitHub repository: {repo_url}? Does this repository count as an imaging software? Here is what “counts” as an imaging software?
            1. A software PRIMARLY used to analyse images or that has to do with image data (it is its main function)
            2. It should be a high-level software (we are not interested in instruments and optics software)
            3. It should be a SOFTWARE that can be used, not just something related to images
            First analyse the README and if you are not sure look at the code.
            You should only reply True if we count it as an imaging software or False if not. No more text.
            """

question_repo_infos = """From the following GitHub repository: {repo_url}, provide me:  

            1. A link to a Docker image related to the project (if available).  
            2. A link to the project's official documentation.  
            3. A direct link to an image related to the project (e.g., logo, before-and-after image). You must inspect the repository to confirm the correct file format (e.g., .png, .jpg, .jpeg, etc.) and ensure the link works with raw.githubusercontent.com. Avoid assumptions about the file format. If this cannot be determined, return an empty string.  
            4. A list of software requirements, including any special third-party dependencies (e.g., CUDA, TensorFlow) and their versions (if specified).  
            5. A link to executable instructions, such as an example notebook or test notebook.  

            Return exactly five strings in the specified order, separated by newlines. If an item is unavailable, return an empty string for that entry. Do not include any additional text or explanations in the output.  
            """

question_repo_description = """From the following GitHub repository: {repo_url} and its README: {readme}, provide me a short description of the project based. 
            You can also read the code of the project if the README is not clear enough.
            Please answer only with a short description around 50 words. No additional text or explanation
            """

system_prompt_jsonLD = """You are an expert in scientific software categorization.  
            The user will provide a GitHub repository's codebase (in a flattened text format) along with metadata and a specific SHACL shape.  
            Your task is to extract and structure the relevant metadata from the repository, ensuring that the output strictly follows the given SHACL shape.  
            Only properties explicitly defined in the SHACL shape may be included in the final JSON-LD output.
            """

system_prompt_json = """You are an expert in scientific software categorization. 
The user will provide a codebase and relevant metadata, and you will extract and populate the following fields based on the provided information. 

Your output must strictly conform to the schema and constraints below:

### **Required Fields and Constraints:**
- `name` (string, **required**): Title of the software.
- `description` (string of max 2000 characters, **required**): A concise description of the software.
- `image` (list of **valid URLs**): A list of representative image URLs of the software.
- `applicationCategory` (list of strings, **optional**): Scientific disciplines or categories that the software belongs to.
- `author` (list of objects, **required**): Each author must be an object containing:
  - `name` (string, **required**)
  - `orcidId` (valid URL, **optional**)
  - `affiliation` (list of strings, **optional**): Institutions the author is affiliated with.
- `relatedToOrganization` (list of strings, **optional**): Institutions associated with the software.
- `softwareRequirements` (list of strings, **optional**): Dependencies or prerequisites for running the software.
- `operatingSystem` (list of strings, **optional**): Compatible operating systems.
- `programmingLanguage` (list of strings, **optional**): Programming languages used in the software.
- `supportingData` (list of objects, **optional**): Each object must contain:
  - `name` (string, **optional**)
  - `description` (string, **optional**)
  - `contentURL` (valid URL, **optional**)
  - `measurementTechnique` (string, **optional**)
  - `variableMeasured` (string, **optional**)
- `codeRepository` (list of **valid URLs**, **required**): URLs of code repositories (e.g., GitHub, GitLab).
- `citation` (list of **valid URLs**, **required**): Academic references or citations.
- `dateCreated` (string, **required, format YYYY-MM-DD**): The date the software was initially created.
- `datePublished` (string, **required, format YYYY-MM-DD**): The date the software was made publicly available.
- `license` (string matching pattern `spdx.org.*`, **required**).
- `url` (valid URL, **required**): The main website or landing page of the software.
- `identifier` (string, **required**): Unique identifier (DOI, UUID, etc.).
- `isAccessibleForFree` (boolean, **optional**): True/False indicating if the software is freely available.
- `isBasedOn` (valid URL, **optional**): A reference to related work/software.
- `isPluginModuleOf` (list of strings, **optional**): Software frameworks the software integrates with.
- `hasDocumentation` (valid URL, **optional**): URL of the official documentation.
- `hasExecutableNotebook` (list of objects, **optional**): Each object must contain:
  - `name` (string, **optional**)
  - `description` (string, **optional**)
  - `url` (valid URL, **required**)
- `hasParameter` (list of objects, **required**): Each object must contain:
  - `name` (string of max 60 characters, **optional**)
  - `description` (string of max 2000 characters, **optional**)
  - `encodingFormat` (valid URL, **optional**)
  - `hasDimensionality` (integer > 0, **optional**)
  - `hasFormat` (string, **optional**)
  - `defaultValue` (string, **optional**)
  - `valueRequired` (boolean, **optional**)
- `hasFunding` (list of objects, **required**): Each object must contain:
  - `identifier` (string, **optional**)
  - `fundingGrant` (string, **optional**)
  - `fundingSource` (object, **optional**):
    - `legalName` (string, **optional**)
    - `hasRorId` (valid URL, **optional**)
- `hasSoftwareImage` (list of objects, **required**): Each object must contain:
  - `name` (string, **optional**)
  - `description` (string, **optional**)
  - `softwareVersion` (string matching pattern `[0-9]+\.[0-9]+\.[0-9]+`, **optional**).
  - `availableInRegistry` (valid URL, **optional**).
- `processorRequirements` (list of strings, **optional**): Minimum processor requirements.
- `memoryRequirements` (integer, **optional**): Minimum memory required (in MB).
- `requiresGPU` (boolean, **optional**): Whether the software requires a GPU.
- `fairLevel` (string, **optional**): FAIR (Findable, Accessible, Interoperable, Reusable) level.
- `graph` (string, **optional**): Graph data representation.
- `conditionsOfAccess` (string, **optional**): Conditions of access to the software (free to access or not for example).
- `featureList` (list of strings, **optional**): List of features representing the Software.
- `isBasedOn` (valid URL, **optional**): The software, website or app the software is based on.
- `isPluginModuleOf` (list of strings, **optional**): The software or app the software is plugin or module of.
- `hasAcknowledgements` (string, **optional**): The acknowledgements of the software.
- `hasExecutableInstructions` (string, **optional**): Any exectuable instructions related to the software.
- `readme` (valid URL, **optional**): README of the software
- `imagingModality (list of strings, **optional**): imaging modalities accepted by the software.

### **⚠️ Important Formatting Rules**
1. **All required fields must be present.**
2. **Fill the not-known optional fields with an empty string or a None if the field is numerical.**
3. **Ensure all URLs are valid URL and strings.**
4. **Follow numerical constraints (`> 0` where specified).**
5. **Ensure software version matches `[0-9]+\.[0-9]+\.[0-9]+` (e.g., `1.2.3`).**
6. **License must start with `spdx.org/`.**
7. **Dates must follow `YYYY-MM-DD` format.**
8. **Output must be well-formatted and valid JSON.**
"""


###########################################


template_is_imaging_software = """Question: {question}
        
        Answer in the following format:
        ```json
        {{
            "repo": "<repo_url>",
            "is_imaging_software": <true_or_false>
        }}
        """
prompt_is_imaging_software = ChatPromptTemplate.from_template(template_is_imaging_software)

template_repo_infos = """Question: {question}
        
        Answer in the following format:
        ```json
        {{
            "repo": "<repo_url>",
            "docker": <docker_url>,
            "documentation": <documentation_url>,
            "image": <image_url>,
            "requirements": <requirements_infos>,
            "executable": <executable_url>
        }}
        """
prompt_repo_infos = ChatPromptTemplate.from_template(template_repo_infos)

template_repo_description = """Question: {question}
        
        Answer in the following format:
        ```json
        {{
            "repo": "<repo_url>",
            "description": <description>
        }}
        """
prompt_repo_description = ChatPromptTemplate.from_template(template_repo_description)