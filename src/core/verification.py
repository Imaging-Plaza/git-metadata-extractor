import re
import requests
import logging
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


class Verification:
    def __init__(self, metadata: dict, repo_url: str = None):
        self.data = metadata
        self.repo_url = repo_url or "unknown"
        self.issues = []
        self.warnings = []
        self.invalid_fields = {}

    def run(self):
        logger.info("Running metadata validation checks...")
        self._check_required_fields()
        self._check_formats()
        self._check_authors()
        self._check_software_images()
        self._check_url_accessibility()

        if not self.issues:
            logger.info("Metadata is valid.")
            return ["✅ Metadata appears valid."]
        else:
            logger.warning(f"{len(self.issues)} validation issue(s) found.")
            return self.issues

    def _check_required_fields(self):
        logger.debug("Checking required fields...")
        required_fields = [
            "name",
            "description",
            "author",
            "codeRepository",
            "citation",
            "dateCreated",
            "datePublished",
            "license",
            "url",
            "identifier",
            "hasSoftwareImage",
        ]
        for field in required_fields:
            value = self.data.get(field)
            if value in [None, "", [], {}]:
                msg = f"Missing required field: {field}"
                logger.error(f"{self.repo_url} :: {msg}")
                self.issues.append(msg)
                self.invalid_fields[field] = "Missing required field"

    def _check_formats(self):
        logger.debug("Checking formats for license, dates, and URLs...")

        # License format
        license_val = self.data.get("license", "")
        if license_val and "spdx.org/licenses/" not in license_val:
            msg = f"License is not a valid SPDX URL: {license_val}"
            logger.error(f"{self.repo_url} :: {msg}")
            self.issues.append(msg)
            self.invalid_fields["license"] = msg

        # Date fields
        for date_field in ["dateCreated", "datePublished"]:
            date_val = self.data.get(date_field)
            if date_val and not self._is_date(date_val):
                msg = f"Invalid date format in {date_field}: {date_val}"
                logger.error(f"{self.repo_url} :: {msg}")
                self.issues.append(msg)
                self.invalid_fields[date_field] = msg

        # Single string URLs
        url_fields = ["url", "readme", "hasDocumentation"]
        for field in url_fields:
            url_val = self.data.get(field)
            if not isinstance(url_val, str) or not self._is_valid_url(url_val):
                msg = f"Invalid or missing URL in {field}: {url_val}"
                logger.error(f"{self.repo_url} :: {msg}")
                self.issues.append(msg)
                self.invalid_fields[field] = msg

        # Lists of URLs
        list_fields = ["codeRepository", "citation"]
        for field in list_fields:
            val = self.data.get(field)
            if not isinstance(val, list):
                msg = f"Expected list in {field}, got {type(val).__name__}"
                logger.error(f"{self.repo_url} :: {msg}")
                self.issues.append(msg)
                self.invalid_fields[field] = msg
                continue

            bad_items = [
                v for v in val if not isinstance(v, str) or not self._is_valid_url(v)
            ]
            if bad_items:
                msg = f"{len(bad_items)} invalid URLs in {field}: {bad_items}"
                logger.error(f"{self.repo_url} :: {msg}")
                self.issues.append(msg)
                self.invalid_fields[field] = bad_items

        # Check image field (list of Image objects with contentUrl and keywords)
        images = self.data.get("image", [])
        if images and not isinstance(images, list):
            msg = f"Expected list in image, got {type(images).__name__}"
            logger.error(f"{self.repo_url} :: {msg}")
            self.issues.append(msg)
            self.invalid_fields["image"] = msg
        elif images:
            bad_images = []
            for img in images:
                if isinstance(img, dict):
                    content_url = img.get("contentUrl")
                    if content_url and not self._is_valid_url(content_url):
                        bad_images.append(img)
                elif isinstance(img, str):
                    # Handle case where it's a plain URL string
                    if not self._is_valid_url(img):
                        bad_images.append(img)
                else:
                    bad_images.append(img)

            if bad_images:
                msg = f"{len(bad_images)} invalid URLs in image: {bad_images}"
                logger.error(f"{self.repo_url} :: {msg}")
                self.issues.append(msg)
                self.invalid_fields["image"] = bad_images

    def _check_authors(self):
        logger.debug("Checking author objects...")
        authors = self.data.get("author", [])
        if not isinstance(authors, list):
            msg = "`author` must be a list"
            logger.error(f"{self.repo_url} :: {msg}")
            self.issues.append(msg)
            self.invalid_fields["author"] = msg
            return

        for author in authors:
            if not isinstance(author, dict):
                msg = f"Invalid author entry (not a dict): {author}"
                logger.error(f"{self.repo_url} :: {msg}")
                self.issues.append(msg)
                continue

            if "name" not in author or not author["name"]:
                msg = "Missing `name` in author object"
                logger.error(f"{self.repo_url} :: {msg}")
                self.issues.append(msg)
                self.invalid_fields.setdefault("author", []).append("Missing name")

            orcid = author.get("orcidId")
            if orcid and not self._is_valid_url(orcid):
                msg = f"Invalid ORCID ID: {orcid}"
                logger.error(f"{self.repo_url} :: {msg}")
                self.issues.append(msg)
                self.invalid_fields.setdefault("author", []).append("Invalid ORCID ID")

    def _check_software_images(self):
        logger.debug("Checking software image objects...")
        images = self.data.get("hasSoftwareImage", [])
        if not isinstance(images, list):
            msg = "`hasSoftwareImage` must be a list"
            logger.error(f"{self.repo_url} :: {msg}")
            self.issues.append(msg)
            self.invalid_fields["hasSoftwareImage"] = msg
            return

        for img in images:
            if not isinstance(img, dict):
                msg = f"Invalid image entry (not a dict): {img}"
                logger.error(f"{self.repo_url} :: {msg}")
                self.issues.append(msg)
                continue

            if "softwareVersion" in img and not self._is_version(
                img["softwareVersion"]
            ):
                msg = f"Invalid softwareVersion: {img['softwareVersion']}"
                logger.error(f"{self.repo_url} :: {msg}")
                self.issues.append(msg)
                self.invalid_fields.setdefault("hasSoftwareImage", []).append(
                    "Invalid version"
                )

            if "availableInRegistry" in img and not self._is_valid_url(
                img["availableInRegistry"]
            ):
                msg = f"Invalid registry URL: {img['availableInRegistry']}"
                logger.error(f"{self.repo_url} :: {msg}")
                self.issues.append(msg)
                self.invalid_fields.setdefault("hasSoftwareImage", []).append(
                    "Invalid URL"
                )

    def _check_url_accessibility(self):
        logger.debug("Checking URL accessibility...")
        url_fields = ["url", "readme", "hasDocumentation"]
        list_fields = ["codeRepository", "citation"]

        all_urls = []

        for field in url_fields:
            val = self.data.get(field)
            if isinstance(val, str):
                all_urls.append(val)

        for field in list_fields:
            urls = self.data.get(field, [])
            if isinstance(urls, list):
                all_urls.extend([u for u in urls if isinstance(u, str)])

        # Handle image field specially (list of Image objects)
        images = self.data.get("image", [])
        if isinstance(images, list):
            for img in images:
                if isinstance(img, dict):
                    content_url = img.get("contentUrl")
                    if content_url and isinstance(content_url, str):
                        all_urls.append(content_url)
                elif isinstance(img, str):
                    all_urls.append(img)

        for url in all_urls:
            if not self._url_responds(url):
                msg = f"Unreachable URL: {url}"
                logger.warning(f"{self.repo_url} :: {msg}")
                self.warnings.append(msg)

    def sanitize_metadata(self):
        logger.info("Sanitizing metadata...")
        clean_data = self.data.copy()

        for field, reason in self.invalid_fields.items():
            if field not in clean_data:
                continue

            if isinstance(reason, str):
                logger.warning(f"Removing invalid field: {field}")
                del clean_data[field]

            elif isinstance(reason, list) and isinstance(clean_data[field], list):
                # Special handling for image field
                if field == "image":
                    valid_images = []
                    for img in clean_data[field]:
                        if isinstance(img, dict):
                            content_url = img.get("contentUrl")
                            if content_url and self._is_valid_url(content_url):
                                valid_images.append(img)
                        elif isinstance(img, str) and self._is_valid_url(img):
                            valid_images.append(img)
                    if valid_images:
                        clean_data[field] = valid_images
                    else:
                        del clean_data[field]
                        logger.warning(f"Removed entire invalid list: {field}")
                else:
                    # For other list fields (plain URL strings)
                    valid_items = [
                        v
                        for v in clean_data[field]
                        if isinstance(v, str) and self._is_valid_url(v)
                    ]
                    if valid_items:
                        clean_data[field] = valid_items
                    else:
                        del clean_data[field]
                        logger.warning(f"Removed entire invalid list: {field}")

            elif field == "author":
                authors = clean_data.get("author", [])
                valid = [a for a in authors if a.get("name")]
                clean_data["author"] = valid if valid else None
                if not valid:
                    del clean_data["author"]
                    logger.warning("Removed invalid author entries.")

            elif field == "hasSoftwareImage":
                imgs = []
                for img in clean_data["hasSoftwareImage"]:
                    if not isinstance(img, dict):
                        continue
                    if "softwareVersion" in img and not self._is_version(
                        img["softwareVersion"]
                    ):
                        del img["softwareVersion"]
                    if "availableInRegistry" in img and not self._is_valid_url(
                        img["availableInRegistry"]
                    ):
                        del img["availableInRegistry"]
                    imgs.append(img)
                clean_data["hasSoftwareImage"] = imgs

        # 🧼 Remove any empty fields
        empty_keys = [k for k, v in clean_data.items() if v in ["", [], {}, [{}]]]
        for k in empty_keys:
            del clean_data[k]
            logger.info(f"Removed empty field: {k}")

        logger.info("Sanitization complete.")
        return clean_data

    def summary(self):
        logger.info("Validation Summary:")
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
            print("✅ All tested links are reachable.")

    def as_dict(self):
        return {
            "status": "valid" if not self.issues else "invalid",
            "issues": self.issues,
            "warnings": self.warnings,
            "invalid_fields": self.invalid_fields,
        }

    # --- Utility methods ---

    def _is_valid_url(self, url):
        try:
            result = urlparse(url)
            return result.scheme in ("http", "https") and bool(result.netloc)
        except Exception:
            return False

    def _url_responds(self, url):
        try:
            response = requests.head(url, timeout=5)
            return response.status_code < 400
        except requests.RequestException:
            return False

    def _is_date(self, date):
        return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", date))

    def _is_version(self, version):
        return bool(re.fullmatch(r"\d+\.\d+\.\d+", version))
