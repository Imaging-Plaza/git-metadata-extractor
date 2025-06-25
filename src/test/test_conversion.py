#!/usr/bin/env python3
"""
Test script for JSON-LD to Zod schema conversion
"""

import json
import sys
import os

# Add src to path
sys.path.append(os.path.join(os.path.dirname(__file__), 'src'))

from src.core.models import convert_jsonld_to_frontend_model

# Your example JSON-LD data
sample_jsonld_data = [
    {
        "@id": "https://github.com/Imaging-Plaza",
        "@type": [
            "http://schema.org/Organization"
        ],
        "http://schema.org/legalName": [
            {
                "@value": "Imaging Plaza"
            }
        ],
        "http://schema.org/logo": [
            {
                "@id": "https://avatars.githubusercontent.com/u/163422059?v=4"
            }
        ],
        "http://schema.org/name": [
            {
                "@value": "Imaging-Plaza"
            }
        ]
    },
    {
        "@id": "https://github.com/qchapp/lungs-segmentation",
        "@type": [
            "http://schema.org/SoftwareSourceCode"
        ],
        "http://schema.org/author": [
            {
                "@id": "https://github.com/qchapp"
            }
        ],
        "http://schema.org/codeRepository": [
            {
                "@id": "https://github.com/qchapp/lungs-segmentation"
            }
        ],
        "http://schema.org/contributor": [
            {
                "@id": "https://github.com/qchapp"
            }
        ],
        "http://schema.org/dateCreated": [
            {
                "@value": "2025-03-10"
            }
        ],
        "http://schema.org/dateModified": [
            {
                "@value": "2025-06-24"
            }
        ],
        "http://schema.org/datePublished": [
            {
                "@value": "2025-03-28"
            }
        ],
        "http://schema.org/description": [
            {
                "@value": "A deep-learning pipeline for automated lung segmentation in mice CT scans, aiding lung cancer research by isolating lung regions for more precise analysis."
            }
        ],
        "http://schema.org/downloadUrl": [
            {
                "@id": "https://github.com/qchapp/lungs-segmentation/archive/refs/tags/v1.0.9.tar.gz"
            }
        ],
        "http://schema.org/license": [
            {
                "@id": "https://spdx.org/licenses/BSD-3-Clause.html"
            }
        ],
        "http://schema.org/name": [
            {
                "@value": "qchapp/lungs-segmentation"
            }
        ],
        "http://schema.org/programmingLanguage": [
            {
                "@value": "Python"
            }
        ],
        "http://schema.org/version": [
            {
                "@value": "v1.0.9"
            }
        ],
        "http://schema.org/applicationCategory": [
            {
                "@value": "Medical Imaging"
            },
            {
                "@value": "Bioinformatics"
            },
            {
                "@value": "Image Processing"
            },
            {
                "@value": "Deep Learning"
            }
        ],
        "http://schema.org/conditionsOfAccess": [
            {
                "@value": "Free to access and use under the BSD-3 license."
            }
        ],
        "http://schema.org/featureList": [
            {
                "@value": "U-Net based lung segmentation"
            },
            {
                "@value": "Binary mask output"
            },
            {
                "@value": "Napari plugin"
            },
            {
                "@value": "Command-line interface (CLI)"
            },
            {
                "@value": "Hugging Face model weights download"
            }
        ],
        "https://w3id.org/okn/o/sd#hasAcknowledgements": [
            {
                "@value": "This project was developed as part of a Bachelor's project at the EPFL Center for Imaging. It was carried out under the supervision of Mallory Wittwer and Edward Andò, whom we sincerely thank for their guidance and support."
            }
        ],
        "https://w3id.org/okn/o/sd#hasDocumentation": [
            {
                "@value": "https://github.com/qchapp/lungs-segmentation/blob/master/README.md"
            }
        ],
        "https://w3id.org/okn/o/sd#hasExecutableInstructions": [
            {
                "@value": "https://github.com/qchapp/lungs-segmentation/blob/master/README.md#installation"
            }
        ],
        "https://imaging-plaza.epfl.ch/ontology#hasExecutableNotebook": [
            {
                "http://schema.org/description": [
                    {
                        "@value": "Notebook analyzing the results of the project by comparing classical approaches with the trained model."
                    }
                ],
                "http://schema.org/name": [
                    {
                        "@value": "Results Analysis"
                    }
                ],
                "http://schema.org/url": [
                    {
                        "@value": "https://github.com/qchapp/lungs-segmentation/blob/master/results.ipynb"
                    }
                ]
            }
        ],
        "https://w3id.org/okn/o/sd#hasParameter": [
            {
                "http://schema.org/defaultValue": [
                    {
                        "@value": "0.5"
                    }
                ],
                "http://schema.org/description": [
                    {
                        "@value": "A float value between 0 and 1 to be applied to the predicted image to obtain a binary mask. Default is 0.5."
                    }
                ],
                "http://schema.org/encodingFormat": [
                    {
                        "@value": "https://en.wikipedia.org/wiki/Float"
                    }
                ],
                "https://w3id.org/okn/o/sd#hasFormat": [
                    {
                        "@value": "float"
                    }
                ],
                "http://schema.org/name": [
                    {
                        "@value": "threshold"
                    }
                ],
                "http://schema.org/valueRequired": [
                    {
                        "@value": False
                    }
                ]
            }
        ],
        "http://schema.org/identifier": [
            {
                "@value": "https://github.com/qchapp/lungs-segmentation"
            }
        ],
        "http://schema.org/image": [
            {
                "@value": "https://raw.githubusercontent.com/qchapp/lungs-segmentation/refs/heads/master/images/main_fig.png"
            },
            {
                "@value": "https://raw.githubusercontent.com/qchapp/lungs-segmentation/refs/heads/master/images/loss.png"
            },
            {
                "@value": "https://raw.githubusercontent.com/qchapp/lungs-segmentation/refs/heads/master/images/lungs1.png"
            },
            {
                "@value": "https://raw.githubusercontent.com/qchapp/lungs-segmentation/refs/heads/master/images/lungs2.png"
            },
            {
                "@value": "https://raw.githubusercontent.com/qchapp/lungs-segmentation/refs/heads/master/images/lungs3.png"
            },
            {
                "@value": "https://raw.githubusercontent.com/qchapp/lungs-segmentation/refs/heads/master/images/lungs4.png"
            },
            {
                "@value": "https://raw.githubusercontent.com/qchapp/lungs-segmentation/refs/heads/master/images/napari-screenshot.png"
            }
        ],
        "https://imaging-plaza.epfl.ch/ontology#imagingModality": [
            {
                "@value": "CT"
            }
        ],
        "http://schema.org/isAccessibleForFree": [
            {
                "@value": True
            }
        ],
        "https://imaging-plaza.epfl.ch/ontology#isPluginModuleOf": [
            {
                "@value": "Napari"
            }
        ],
        "https://w3id.org/okn/o/sd#readme": [
            {
                "@value": "https://github.com/qchapp/lungs-segmentation/blob/master/README.md"
            }
        ],
        "https://imaging-plaza.epfl.ch/ontology#relatedToOrganization": [
            {
                "@value": "EPFL Center for Imaging"
            }
        ],
        "https://imaging-plaza.epfl.ch/ontology#requiresGPU": [
            {
                "@value": True
            }
        ],
        "http://schema.org/softwareRequirements": [
            {
                "@value": "python>=3.9"
            },
            {
                "@value": "pytorch>=2.0"
            },
            {
                "@value": "napari[all]==0.4.18"
            },
            {
                "@value": "scikit-image==0.22.0"
            },
            {
                "@value": "tifffile==2023.9.18"
            },
            {
                "@value": "matplotlib==3.8.2"
            },
            {
                "@value": "csbdeep==0.7.4"
            },
            {
                "@value": "python-dotenv==1.0.0"
            },
            {
                "@value": "huggingface_hub==0.29.3"
            }
        ],
        "http://schema.org/supportingData": [
            {
                "http://schema.org/description": [
                    {
                        "@value": "355 images from 17 different experiments and 2 different scanners used for training the model."
                    }
                ],
                "http://schema.org/measurementTechnique": [
                    {
                        "@value": "CT scans"
                    }
                ],
                "http://schema.org/name": [
                    {
                        "@value": "Training Dataset"
                    }
                ],
                "http://schema.org/variableMeasured": [
                    {
                        "@value": "Mouse lung CT scans"
                    }
                ]
            },
            {
                "http://schema.org/description": [
                    {
                        "@value": "62 images used for validating the model."
                    }
                ],
                "http://schema.org/measurementTechnique": [
                    {
                        "@value": "CT scans"
                    }
                ],
                "http://schema.org/name": [
                    {
                        "@value": "Validation Dataset"
                    }
                ],
                "http://schema.org/variableMeasured": [
                    {
                        "@value": "Mouse lung CT scans"
                    }
                ]
            }
        ],
        "http://schema.org/url": [
            {
                "@value": "https://github.com/qchapp/lungs-segmentation"
            }
        ]
    },
    {
        "@id": "https://github.com/qchapp",
        "@type": [
            "http://schema.org/Person"
        ],
        "http://schema.org/affiliation": [
            {
                "@id": "https://github.com/Imaging-Plaza"
            }
        ],
        "http://schema.org/identifier": [
            {
                "@value": "qchapp"
            }
        ],
        "http://schema.org/name": [
            {
                "@value": "Quentin"
            }
        ]
    }
]

def main():
    print("🔄 Converting JSON-LD to Zod-compatible format...\n")
    
    # Convert the data
    converted = convert_jsonld_to_frontend_model(sample_jsonld_data)
    
    print("📊 Conversion Results:")
    print(f"- Software Source Codes: {len(converted['softwareSourceCodes'])}")
    print(f"- Persons: {len(converted['persons'])}")
    print(f"- Organizations: {len(converted['organizations'])}")
    print(f"- Data Feeds: {len(converted['dataFeeds'])}")
    print(f"- Executable Notebooks: {len(converted['executableNotebooks'])}")
    print(f"- Parameters: {len(converted['parameters'])}")
    print(f"- Software Images: {len(converted['softwareImages'])}")
    print()
    
    # Show the converted software
    if converted['softwareSourceCodes']:
        print("🔧 Converted SoftwareSourceCode (Zod-compatible):")
        software = converted['softwareSourceCodes'][0]
        print(json.dumps(software, indent=2))
        print()
    
    # Show the converted person
    if converted['persons']:
        print("👤 Converted Person (Zod-compatible):")
        person = converted['persons'][0]
        print(json.dumps(person, indent=2))
        print()
    
    # Show the converted organization
    if converted['organizations']:
        print("🏢 Converted Organization (Zod-compatible):")
        org = converted['organizations'][0]
        print(json.dumps(org, indent=2))
        print()
    
    print("✅ Conversion completed successfully!")
    print("\n📋 Field mappings applied:")
    print("- JSON-LD URIs → Zod schema field names")
    print("- @value/@id extraction → Direct values")
    print("- Array flattening for single values")
    print("- Nested object conversion")

if __name__ == "__main__":
    main()
