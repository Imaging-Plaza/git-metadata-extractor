"""
This script is used to automatically tag image analysis projects listed as `software tools` on https://imaging.epfl.ch/field-guide/index.html.
"""

import sys
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field
from typing import List
from github import Github

g = Github()

AVAILABLE_USED_FOR_TAGS = [
    "Segmentation",
    "Registration",
    "Detection",
    "Tracking",
    "Visualization",
    "Reconstruction",
    "Optimization",
    "Restoration",
]

AVAILABLE_KEYWORDS = [
    "Python",
    "Java",
    "C++",
    "ImageJ/Fiji",
    "QuPath",
    "Napari",
    "Blender",
    "Web app",
    "Desktop app",
    "EPFL",
    "Cell biology",
    "Neurobiology",
    "Medical imaging",
    "Digital pathology",
    "Spatial transcriptomics",
    "Astronomy",
    "Materials science",
    "Animal behavior",
    "Fluorescence microscopy",
    "Electron microscopy",
    "Deep learning",
]


class RepoContent(BaseModel):
    """Content of the repository"""

    repository_name: str = Field(
        description="The name of the project featured in the repository."
    )
    repository_description: str = Field(
        description="A single, short, and catchy sentence summarizing of the repository's project."
    )
    used_for_tags: List[str] = Field(
        ...,
        description="""
        A list of tags representing image analysis tasks that the project is intended to be used for.\n
        The tags MUST be part of this list: [
            "Segmentation",
            "Registration",
            "Detection",
            "Tracking",
            "Visualization",
            "Reconstruction",
            "Optimization",
            "Restoration",
        ].
        For example, a repository featuring an image segmentation project should be tagged `Segmentation`.\n
        A repository featuring a project intended for image segmentation and registration should be tagged `Segmentation` and `Registration`.\n
        A repository featuring no usage related to any of the listed tags should not contain any tags.\n
        """,
    )
    keywords: List[str] = Field(
        ...,
        description="""
        A list of keywords related to the project.\n
        The kewords MUST be part of this list: [
            "Python",
            "Java",
            "C++",
            "ImageJ/Fiji",
            "QuPath",
            "Napari",
            "Blender",
            "Web app",
            "Desktop app",
            "EPFL",
            "Cell biology",
            "Neurobiology",
            "Medical imaging",
            "Digital pathology",
            "Spatial transcriptomics",
            "Astronomy",
            "Materials science",
            "Animal behavior",
            "Fluorescence microscopy",
            "Electron microscopy",
            "Deep learning",
        ].
        For example, a repository presenting an algorithm intended for cell nuclei segmentation in whole slide images should include the keywords `Cell biology` and `Digital pathology`.
        If the repository is unrelated to any of the available keywords, then it should not contain any keywords.
        """,
    )
    suggested_extra_keywords: str = Field(
        description="""
        A string representation of suggested extra keywords for the repository. \n
        If the project featured in the repository is strongly related to an important concept, such as a programming language (example: Javascript), an existing image analysis software (example: CellProfiler),
        a software category (example: remote API), an imaging field (example: Geospatial) or a microscopy technique (example: Holography), and if this concept is not already part of the keywords, then it should
        be listed as a suggested extra keyword. There can be multiple (but no more than a few) suggested extra keywords. Suggested extra keywords should be separated by commas.
        An example of a valid string of suggested extra keywords is: `Javascript, CellProfiler, Geospatial, Holography`.
        """
    )


model = ChatOpenAI(model="gpt-4o-mini").with_structured_output(RepoContent)


def parse_github_repo(repo_url: str) -> RepoContent | None:
    if "github.com" not in repo_url:
        return None

    repo_organization, repo_name = repo_url.split("/")[-2:]
    repo = g.get_repo(f"{repo_organization}/{repo_name}")
    if repo is None:
        print("Could not find this repository.")
        return None

    repo_name = repo.name

    repo_description = repo.description  # Not used (yet)

    if repo.organization is not None:
        repo_organization = repo.organization.name
    else:
        repo_organization = ""

    # Not used yet, but could be useful
    if repo.license is not None:
        repo_license = repo.license.name
    else:
        repo_license = ""

    repo_topics = repo.topics

    contents = repo.get_contents("")  # Contents of the root directory of the repository
    readme_content = None
    for content_file in contents:
        if content_file.name.lower().startswith("readme"):
            readme_content = content_file.decoded_content.decode("utf-8")
    if readme_content is None:
        print("Could not find a readme file.")
        return None

    system_template = """
        Answer the user query. Output your answer as JSON that matches the given schema: \`\`\`json\n{schema}\n\`\`\`.
        Make sure to wrap the answer in \`\`\`json and \`\`\` tags.
        Here is what the `Used for tags` refer to:
            - Segmentation: image segmentation, instance segmentation, binary masks, segmentation masks, panoptic segmentation, thresholding
            - Registration: image registration, aligning images, optical flow, DIC, digital image correlation, registering images, image stack alignment, PIV
            - Detection: object detection, bounding box detection, keypoint detection, blob detection, line detection, Yolo models
            - Tracking: object tracking, multi-object tracking, particle tracking, cell tracking, linkage, Hungarian algorithm, Bayesian algorithm
            - Visualization: image visualization, image plotting, data visualization, 3D rendering
            - Reconstruction: image reconstruction, inverse problems, computational image reconstruction, Richardson-Lucy, deconvolution
            - Optimization: code optimization, performance, speed, numba, GPU computing, big data browsing
            - Restoration: image denoising, deblurring, image restoration, artefacts removal
        Here is what the `Keywords` refer to:
            - Python: the project is primarily implemented in Python.
            - Java: the project is primarily implemented in Java.
            - C++: the project is primarily implemented in C++.
            - ImageJ/Fiji: the project is a Fiji plugin.
            - QuPath: the project is a QuPath extension.
            - Napari: the project is a Napari plugin.
            - Blender: the project uses Blender.
            - Web app: the project is a web application.
            - Desktop app: the project can be installed as a Desktop application or provides a GUI.
            - EPFL: the project was developed, at least in part, at the EPFL (Ecole Polytechnique Federale de Lausanne).
            - Cell biology: the project is intended for cell biology analysis. Cell nuclei. Membranes.
            - Neurobiology: the project is intended for neurobiology analysis.
            - Medical imaging: the project is intended for medical imaging analysis. CT scans, MRI, computed tomography.
            - Digital pathology: whole slide imaging, H&E stains, KI67 staining, QuPath, digital scanner.
            - Spatial transcriptomics
            - Astronomy: astrophysics, telescope, astroimaging, galaxy
            - Materials science: pores analysis, granular materials, phase separation, metallurgy, metallography
            - Animal behavior: DeepLabCut, animal tracking, wildlife, wilderness, mouse, fly
            - Fluorescence microscopy
            - Electron microscopy: EM microscopy, TEM, SEM, HRTEM, HAADF, scanning TEM
            - Deep learning: machine learning, pytorch, tensorflow, model training, inference
    """

    user_prompt = """
        Here is some information about the repository:\n
        Repository topics: {repo_topics}\n
        Repository's README file: {readme_content}\n
    """

    prompt = ChatPromptTemplate.from_messages(
        [("system", system_template), ("user", user_prompt)]
    ).partial(schema=RepoContent.schema())

    chain = prompt | model

    repo_content = chain.invoke(
        {
            "repo_topics": repo_topics,
            "readme_content": readme_content,
        }
    )

    parsed_used_for_tags = repo_content.used_for_tags
    parsed_keywords = repo_content.keywords
    parsed_suggested_extra_keywords = repo_content.suggested_extra_keywords

    validated_used_for_tags = [
        tag for tag in parsed_used_for_tags if tag in AVAILABLE_USED_FOR_TAGS
    ]
    validated_keywords = [
        keyword for keyword in parsed_keywords if keyword in AVAILABLE_KEYWORDS
    ]
    validated_suggested_extra_keywords = (
        parsed_suggested_extra_keywords.split(", ")
        + [keyword for keyword in parsed_keywords if keyword not in AVAILABLE_KEYWORDS]
        + [tag for tag in parsed_used_for_tags if tag not in AVAILABLE_USED_FOR_TAGS]
    )
    validated_suggested_extra_keywords = ", ".join(validated_suggested_extra_keywords)

    validated_repo_content = RepoContent(
        repository_name=repo_content.repository_name,
        repository_description=repo_content.repository_description,
        used_for_tags=validated_used_for_tags,
        keywords=validated_keywords,
        suggested_extra_keywords=validated_suggested_extra_keywords,
    )

    return validated_repo_content


if __name__ == "__main__":
    # Example on a github repo URL provided as input
    _, repo_url = sys.argv
    repo_content = parse_github_repo(repo_url)
    print(repo_content)
