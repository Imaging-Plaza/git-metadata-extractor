import re
import requests
import logging
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

class Verification:
    def __init__(self, metadata: dict):
        """
        Initialize the Verification instance with metadata to validate.
        """
        self.data = metadata  # Original metadata dictionary
        self.issues = []  # Critical validation failures
        self.warnings = []  # Non-blocking warnings (like unreachable URLs)
        self.invalid_fields = {}  # Fields to sanitize after validation

    def run(self):
        """
        Run the full validation suite on the metadata.
        """
        logger.info("Running metadata validation checks...")
        self._check_required_fields()
        self._check_formats()
        self._check_authors()
        self._check_software_images()
        self._check_url_accessibility()

        # Return overall result
        if not self.issues:
            logger.info("Metadata is valid.")
            return ["✅ Metadata appears valid."]
        else:
            logger.warning(f"{len(self.issues)} validation issue(s) found.")
            return self.issues

    def _check_required_fields(self):
        """
        Ensure all required fields are present and not empty.
        """
        logger.debug("Checking required fields...")
        required_fields = [
            "name", "description", "author", "codeRepository", "citation",
            "dateCreated", "datePublished", "license", "url", "identifier", 
            "hasSoftwareImage", "hasParameter", "hasFunding"
        ]
        for field in required_fields:
            value = self.data.get(field)
            if value in [None, "", [], {}]:
                msg = f"Missing required field: {field}"
                logger.error(msg)
                self.issues.append(msg)
                self.invalid_fields[field] = "Missing required field"

    def _check_formats(self):
        """
        Validate formatting of license, dates, and various URL fields.
        """
        logger.debug("Checking field formats...")

        # SPDX license check (must include SPDX URL)
        license_val = self.data.get("license", "")
        if license_val and "spdx.org/licenses/" not in license_val:
            msg = f"License is not a valid SPDX URL: {license_val}"
            logger.error(msg)
            self.issues.append(msg)
            self.invalid_fields["license"] = msg

        # ISO date format check for creation and publication dates
        for date_field in ["dateCreated", "datePublished"]:
            date_val = self.data.get(date_field)
            if date_val and not self._is_date(date_val):
                msg = f"Invalid date format in {date_field}: {date_val}"
                logger.error(msg)
                self.issues.append(msg)
                self.invalid_fields[date_field] = msg

        # Single URL fields
        url_fields = ["url", "readme", "hasDocumentation"]
        for field in url_fields:
            url_val = self.data.get(field)
            if url_val and not self._is_valid_url(url_val):
                msg = f"Invalid or unreachable URL in {field}: {url_val}"
                logger.error(msg)
                self.issues.append(msg)
                self.invalid_fields[field] = msg

        # List fields with URLs
        for list_field in ["codeRepository", "citation", "image"]:
            for url in self.data.get(list_field, []):
                if not self._is_valid_url(url):
                    msg = f"Invalid or unreachable URL in {list_field}: {url}"
                    logger.error(msg)
                    self.issues.append(msg)
                    self.invalid_fields.setdefault(list_field, []).append(url)

    def _check_authors(self):
        """
        Validate structure and values in the author list.
        """
        logger.debug("Checking author fields...")
        authors = self.data.get("author", [])
        if not isinstance(authors, list):
            msg = "`author` must be a list"
            logger.error(msg)
            self.issues.append(msg)
            self.invalid_fields["author"] = msg
            return

        for author in authors:
            # Name is required for each author
            if "name" not in author:
                msg = "Missing `name` in author object"
                logger.error(msg)
                self.issues.append(msg)
                self.invalid_fields.setdefault("author", []).append("Missing name")

            # ORCID must be a valid URL if provided
            orcid = author.get("orcidId")
            if orcid and not self._is_valid_url(orcid):
                msg = f"Invalid ORCID ID URL: {orcid}"
                logger.error(msg)
                self.issues.append(msg)
                self.invalid_fields.setdefault("author", []).append("Invalid ORCID ID")

    def _check_software_images(self):
        """
        Validate entries in hasSoftwareImage, including version and registry URL.
        """
        logger.debug("Checking software image versions and registry URLs...")
        images = self.data.get("hasSoftwareImage", [])
        if not isinstance(images, list):
            msg = "`hasSoftwareImage` must be a list"
            logger.error(msg)
            self.issues.append(msg)
            self.invalid_fields["hasSoftwareImage"] = msg
            return

        for img in images:
            if "softwareVersion" in img and not self._is_version(img["softwareVersion"]):
                msg = f"Invalid softwareVersion: {img['softwareVersion']}"
                logger.error(msg)
                self.issues.append(msg)
                self.invalid_fields.setdefault("hasSoftwareImage", []).append("Invalid softwareVersion")

            if "availableInRegistry" in img and not self._is_valid_url(img["availableInRegistry"]):
                msg = f"Invalid registry URL: {img['availableInRegistry']}"
                logger.error(msg)
                self.issues.append(msg)
                self.invalid_fields.setdefault("hasSoftwareImage", []).append("Invalid registry URL")

    def _check_url_accessibility(self):
        """
        Make HEAD requests to check if URLs are reachable.
        """
        logger.debug("Checking if URLs are accessible...")
        all_urls = []
        all_urls += self.data.get("image", [])
        all_urls += self.data.get("codeRepository", [])
        all_urls += self.data.get("citation", [])
        for field in ["url", "readme", "hasDocumentation"]:
            val = self.data.get(field)
            if val:
                all_urls.append(val)

        for url in all_urls:
            if not self._url_responds(url):
                msg = f"URL does not resolve (might be broken or slow): {url}"
                logger.warning(msg)
                self.warnings.append(msg)

    def sanitize_metadata(self):
        """
        Remove or fix fields identified as invalid after validation.
        """
        logger.info("Sanitizing metadata based on validation results.")
        clean_data = self.data.copy()

        for field, reason in self.invalid_fields.items():
            if field in clean_data:
                # Remove simple fields
                if isinstance(reason, str):
                    logger.warning(f"Removing invalid field `{field}`: {reason}")
                    del clean_data[field]

                # Remove invalid items from URL lists
                elif isinstance(reason, list) and isinstance(clean_data[field], list):
                    original_len = len(clean_data[field])
                    valid_items = [item for item in clean_data[field] if self._is_valid_url(item)]
                    if valid_items:
                        clean_data[field] = valid_items
                        logger.warning(f"Removed {original_len - len(valid_items)} invalid entries from `{field}`.")
                    else:
                        logger.warning(f"Removing entire list `{field}` (no valid entries).")
                        del clean_data[field]

                # Clean author entries
                elif field == "author":
                    valid_authors = []
                    for author in clean_data["author"]:
                        if "name" in author and author["name"]:
                            if "orcidId" in author and not self._is_valid_url(author["orcidId"]):
                                del author["orcidId"]
                            valid_authors.append(author)
                    if valid_authors:
                        clean_data["author"] = valid_authors
                    else:
                        logger.warning("Removing `author` field entirely (no valid entries).")
                        del clean_data["author"]

                # Clean software image entries
                elif field == "hasSoftwareImage":
                    valid_images = []
                    for img in clean_data["hasSoftwareImage"]:
                        if "softwareVersion" in img and not self._is_version(img["softwareVersion"]):
                            del img["softwareVersion"]
                        if "availableInRegistry" in img and not self._is_valid_url(img["availableInRegistry"]):
                            del img["availableInRegistry"]
                        valid_images.append(img)
                    clean_data["hasSoftwareImage"] = valid_images

        logger.info("Sanitization complete.")
        return clean_data

    def summary(self):
        """
        Print a human-readable summary of validation results.
        """
        logger.info("Printing validation summary.")
        print("\n🔍 Validation Summary:\n")
        if self.issues:
            for issue in self.issues:
                print(f"❌ {issue}")
        else:
            print("✅ No critical issues found.")

        if self.warnings:
            for warn in self.warnings:
                print(f"⚠️ {warn}")
        else:
            print("✅ All links are reachable (or not provided).")

    def as_dict(self):
        """
        Export validation results in a dictionary format (for logging or output).
        """
        return {
            "status": "valid" if not self.issues else "invalid",
            "issues": self.issues,
            "warnings": self.warnings,
            "invalid_fields": self.invalid_fields
        }

    # --- Utility functions ---

    def _is_valid_url(self, url):
        """
        Validate if a string is a well-formed HTTP or HTTPS URL.
        """
        try:
            result = urlparse(url)
            return result.scheme in ("http", "https") and bool(result.netloc)
        except:
            return False

    def _url_responds(self, url):
        """
        Check if a URL resolves with an HTTP HEAD request.
        """
        try:
            response = requests.head(url, timeout=5)
            return response.status_code < 400
        except requests.RequestException:
            return False

    def _is_date(self, date):
        """
        Check if a string matches YYYY-MM-DD format.
        """
        return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", date))

    def _is_version(self, version):
        """
        Check if a version matches X.Y.Z format (semver or date-like).
        """
        return bool(re.fullmatch(r"\d+\.\d+\.\d+", version))
