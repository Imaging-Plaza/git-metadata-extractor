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

system_prompt = """You are an expert in scientific software categorization.  
            The user will provide a GitHub repository's codebase (in a flattened text format) along with metadata and a specific SHACL shape.  
            Your task is to extract and structure the relevant metadata from the repository, ensuring that the output strictly follows the given SHACL shape.  
            Only properties explicitly defined in the SHACL shape may be included in the final JSON-LD output.
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