question_is_imaging_software = """I need you to analyse this GitHub repository: {repo_url}? Does this repository count as an imaging software? Here is what “counts” as an imaging software?
            1. A software PRIMARLY used to analyse images or that has to do with image data (it is its main function)
            2. It should be a high-level software (we are not interested in instruments and optics software)
            3. It should be a SOFTWARE that can be used, not just something related to images
            First analyse the README and if you are not sure look at the code.
            You should only reply True if we count it as an imaging software or False if not. No more text.
            """

question_repo_infos = """From the following GitHub repository: {repo_url}, provide me:

            1. Using the GitHub repository's README, find a citation link to a paper associated with the project in the form of a DOI URL (e.g. from a published paper or an arxiv.org link related to the project present in the README). Only provide the DOI URL and no additional text.
            2. A link to the project's official documentation.
            3. Retrieve the SPDX (https://spdx.org/licenses/) license URL corresponding to the license: "{license}" (must be a spdx.org URL).
            Only provide the three URLs, no additional text or explanation.
            """