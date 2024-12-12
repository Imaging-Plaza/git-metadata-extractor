question_is_imaging_software = """I need you to analyse this GitHub repository: {repo_url}? Does this repository count as an imaging software? Here is what “counts” as an imaging software?
            1. A software PRIMARLY used to analyse images or that has to do with image data (it is its main function)
            2. It should be a high-level software (we are not interested in instruments and optics software)
            3. It should be a SOFTWARE that can be used, not just something related to images
            First analyse the README and if you are not sure look at the code.
            You should only reply True if we count it as an imaging software or False if not. No more text.
            """

question_repo_infos = """From the following GitHub repository: {repo_url}, provide me:

            1. The project's citation DOI link (MUST be a doi.org URL).
            2. A link to the project's official documentation.
            3. Retrieve the SPDX (https://spdx.org/licenses/) license URL corresponding to the license: "{license}" (MUST be a spdx.org URL).
            Only provide the three URLs, no additional text or explanation.
            """