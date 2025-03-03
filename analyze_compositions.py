# Code is heavily generated by Github Copilot powered by Claude 3.5 Sonnet

import os
import re
from pathlib import Path
from typing import Any, Dict, List

import click
import pandas as pd
import yaml
from loguru import logger
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn


class CompositionExtractor:
    def __init__(self, folder_path: str, verbose: bool = False):
        """
        Initialize the Composition Extractor.

        Args:
            folder_path (str): Path to the folder containing Crossplane manifests
            verbose (bool): Enable verbose logging
        """
        self.folder_path = Path(folder_path)
        self.verbose = verbose
        self.console = Console()

        # Setup logging
        logger.remove()
        logger.add(
            "composition_extraction.log",
            format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {file}:{line} | {function} | {message}",
            level="DEBUG" if verbose else "INFO",
        )

        # Storage for extracted data
        self.extracted_data = []
        self.unique_functions = set()  # Add this line to store unique functions
        logger.debug(
            f"Initialized CompositionExtractor with folder_path: {folder_path}"
        )

    def _is_crossplane_or_upbound_apiversion(self, api_version: str) -> bool:
        """
        Check if the apiVersion contains .crossplane.io or .upbound.io

        Args:
            api_version (str): API version to check

        Returns:
            bool: True if the API version matches the criteria, False otherwise
        """
        return ".crossplane.io/" in api_version or ".upbound.io/" in api_version

    def _extract_template_content(self, template_str: str) -> List[Dict[str, Any]]:
        """
        Extract YAML content from within a Go template string.

        Args:
            template_str (str): The Go template string to parse

        Returns:
            List[Dict[str, Any]]: List of parsed YAML documents from the template
        """
        logger.debug("Starting template content extraction")
        try:
            yaml_docs = []
            current_doc = []
            lines = template_str.split("\n")
            in_yaml = False

            # Define markers that indicate start of a resource
            resource_markers = [
                ("apiVersion:", "kind:"),
                ("apiVersion:", "metadata:"),
            ]

            for line in lines:
                if line.strip().startswith("#"):
                    continue

                # Check for start of a new resource
                for start_marker, confirm_marker in resource_markers:
                    if start_marker in line:
                        next_lines = "".join(
                            lines[lines.index(line) : lines.index(line) + 3]
                        )
                        if confirm_marker in next_lines:
                            if in_yaml and current_doc:
                                yaml_docs.append(
                                    self._parse_templated_yaml(" ".join(current_doc))
                                )
                            current_doc = []
                            in_yaml = True
                            break

                if in_yaml:
                    current_doc.append(line)

                if line.strip() == "---" and in_yaml:
                    if current_doc:
                        yaml_docs.append(
                            self._parse_templated_yaml(" ".join(current_doc))
                        )
                    current_doc = []
                    in_yaml = False

            # Handle last document
            if current_doc:
                yaml_docs.append(self._parse_templated_yaml(" ".join(current_doc)))

            logger.debug(f"Extracted {len(yaml_docs)} YAML documents from template")
            return [doc for doc in yaml_docs if doc]
        except Exception as e:
            logger.error(f"Error extracting template content: {str(e)}")
            return []

    def _parse_templated_yaml(self, content: str) -> Dict[str, Any]:
        """Parse YAML-like content that contains Go template syntax."""
        logger.debug("Parsing templated YAML content")
        try:
            result = {}

            # Simplified regex patterns to capture only the values
            api_match = re.search(r"apiVersion:\s*([^\s\n{]+)", content)
            kind_match = re.search(r"kind:\s*([^\s\n{]+)", content)

            if api_match and kind_match:
                api_version = api_match.group(1).strip()
                kind = kind_match.group(1).strip()

                # Only include these two fields in a cleaner format
                result = {"apiVersion": api_version, "kind": kind}

            return result
        except Exception as e:
            logger.debug(
                f"Failed to parse templated YAML: {str(e)}\nContent: {content}\n "
            )
            return {}

    def _get_api_category(self, api_version: str) -> str:
        """
        Extract category from API version based on immediate prefix before .crossplane.io or .upbound.io

        Args:
            api_version (str): API version string (e.g., 'aws.upbound.io/v1beta1' or 'fn.crossplane.io/v1beta1')

        Returns:
            str: Category name or 'other' if no match
        """
        logger.debug(f"Getting API category for: {api_version}")
        # Handle empty or invalid input
        if not api_version:
            return "other"

        # Match the pattern before .crossplane.io or .upbound.io
        crossplane_match = re.search(r"([^.]+)\.crossplane\.io", api_version)
        upbound_match = re.search(r"([^.]+)\.upbound\.io", api_version)

        if crossplane_match:
            return crossplane_match.group(1)
        elif upbound_match:
            return upbound_match.group(1)
        return "other"

    def _extract_composition_details(
        self, doc: Dict[str, Any], file_path: Path
    ) -> List[Dict[str, Any]]:
        """
        Extract details from a Composition manifest.

        Args:
            doc (Dict[str, Any]): Parsed YAML document
            file_path (Path): Path to the source file

        Returns:
            List[Dict[str, Any]]: Extracted composition details
        """
        logger.debug(f"Extracting composition details from {file_path}")

        # Check if this is a Composition
        if (
            doc.get("apiVersion") != "apiextensions.crossplane.io/v1"
            or doc.get("kind") != "Composition"
        ):
            logger.debug("Document is not a Composition, skipping")
            return []

        # Initialize extraction results
        extraction_results = []

        # Extract compositeRef details
        composite_ref = doc.get("spec", {}).get("compositeTypeRef", {})
        composite_kind_api = f"{composite_ref.get('kind', 'N/A')}_{composite_ref.get('apiVersion', 'N/A')}"

        # Extract function references from pipeline
        function_refs = [
            func.get("functionRef", {}).get("name", "N/A")
            for func in doc.get("spec", {}).get("pipeline", [])
        ]

        # Find apiVersions with .crossplane.io or .upbound.io
        special_resources = []

        def _recursive_search(obj: Any, path: str = ""):
            if isinstance(obj, dict):
                for key, value in obj.items():
                    # Check for apiVersion
                    if key == "apiVersion" and isinstance(value, str):
                        if self._is_crossplane_or_upbound_apiversion(value):
                            # Look for corresponding kind at the same level
                            kind = obj.get("kind", "N/A")
                            special_resources.append(
                                {
                                    "kind_api_version": f"{kind}_{value}",
                                    "kind": kind,
                                    "api_version": value,
                                    "category": self._get_api_category(
                                        value
                                    ),  # Add category
                                }
                            )

                    # Recursively search nested dictionaries and lists
                    next_path = f"{path}.{key}" if path else key
                    _recursive_search(value, next_path)

            elif isinstance(obj, list):
                for idx, item in enumerate(obj):
                    next_path = f"{path}[{idx}]"
                    _recursive_search(item, next_path)

        # Search the entire document for special apiVersions
        _recursive_search(doc)

        # Also search within template strings
        pipeline_steps = doc.get("spec", {}).get("pipeline", [])
        for step in pipeline_steps:
            template_content = (
                step.get("input", {}).get("inline", {}).get("template", "")
            )
            if template_content:
                template_docs = self._extract_template_content(template_content)
                for template_doc in template_docs:
                    _recursive_search(template_doc)

        # If no special resources found, add a placeholder
        if not special_resources:
            special_resources = [
                {"kind_api_version": "N/A", "kind": "N/A", "api_version": "N/A"}
            ]

        # Store unique functions separately instead of adding to each row
        self.unique_functions.update(function_refs)

        # Remove function references from extraction results
        for resource in special_resources:
            extraction_results.append(
                {
                    "File Path": str(file_path),
                    "Compositioin Kind/API Version": composite_kind_api,
                    "ManangedResource (MR) Kind/API Version": resource[
                        "kind_api_version"
                    ],
                    "Kind": resource["kind"],
                    "API Version": resource["api_version"],
                    "Category": resource["category"],  # Add category to final output
                }
            )

        logger.debug(f"Found {len(special_resources)} special resources")
        logger.debug(f"Function references found: {function_refs}")
        return extraction_results

    def extract_compositions(self):
        """
        Recursively find and extract details from Composition manifests.
        """
        logger.debug("Starting composition extraction process")
        yaml_files = list(self.folder_path.rglob("*"))
        yaml_files = [f for f in yaml_files if str(f).endswith((".yaml", ".yml"))]
        logger.debug(f"Found {len(yaml_files)} YAML files to process")

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=self.console,
        ) as progress:
            task = progress.add_task(
                "[cyan]Extracting Compositions...", total=len(yaml_files)
            )

            for file_path in yaml_files:
                try:
                    with open(file_path, "r") as f:
                        content = f.read()

                    # Split multi-document YAML files
                    yaml_docs = yaml.safe_load_all(content)

                    for doc in yaml_docs:
                        if doc:
                            results = self._extract_composition_details(doc, file_path)
                            self.extracted_data.extend(results)

                except Exception as e:
                    logger.error(f"Error processing {file_path}: {str(e)}")

                progress.advance(task)

    def _get_mr_statistics(self) -> pd.DataFrame:
        """Generate statistics for Managed Resources."""
        logger.debug("Generating MR statistics")
        if not self.extracted_data:
            logger.debug("No data available for statistics")
            return pd.DataFrame()

        # Convert to DataFrame for easier analysis
        df = pd.DataFrame(self.extracted_data)

        # Group by MR details and count occurrences
        mr_stats = (
            df.groupby(
                [
                    "ManangedResource (MR) Kind/API Version",
                    "Kind",
                    "API Version",
                    "Category",
                ]
            )
            .agg(
                {
                    "File Path": ["count", lambda x: len(x.unique())],
                    "Compositioin Kind/API Version": lambda x: len(x.unique()),
                }
            )
            .reset_index()
        )

        # Rename columns for clarity
        mr_stats.columns = [
            "Kind/API Version",
            "Kind",
            "API Version",
            "Category",
            "Total Occurrences",
            "Found in N Files",
            "Used by N Compositions",
        ]

        logger.debug(f"Generated statistics for {len(mr_stats)} managed resources")
        return mr_stats.sort_values("Total Occurrences", ascending=False)

    def _get_file_mapping(self) -> pd.DataFrame:
        """Create mapping of MRs to their file locations."""
        logger.debug("Creating file mapping")
        if not self.extracted_data:
            logger.debug("No data available for file mapping")
            return pd.DataFrame()

        df = pd.DataFrame(self.extracted_data)

        # Create a dictionary to store file occurrences
        file_mapping = {}

        # Group by MR and aggregate file information
        for mr, group in df.groupby("ManangedResource (MR) Kind/API Version"):
            # Count occurrences per file
            file_counts = group["File Path"].value_counts()

            # Create formatted string with file paths and counts
            file_info = "\n".join(
                [f"{path} ({count} occurrences)" for path, count in file_counts.items()]
            )

            file_mapping[mr] = {
                "Kind/API Version": mr,
                "Total Files": len(file_counts),
                "Total Occurrences": file_counts.sum(),
                "File Locations": file_info,
            }

        logger.debug(f"Created mapping for {len(file_mapping)} managed resources")
        return pd.DataFrame.from_dict(file_mapping, orient="index")

    def save_to_excel(self, output_file: str = "composition_extraction.xlsx"):
        """Save extracted data to an Excel file with multiple sheets."""
        logger.debug(f"Starting Excel export to {output_file}")
        if not self.extracted_data:
            logger.warning("No data extracted. Skipping Excel export.")
            return

        with pd.ExcelWriter(output_file, engine="xlsxwriter") as writer:
            workbook = writer.book

            # Define formats
            wrap_format = workbook.add_format({"text_wrap": True, "valign": "top"})

            # Main data sheet
            df = pd.DataFrame(self.extracted_data)
            df.to_excel(writer, sheet_name="Raw Data", index=False)

            # MR Statistics sheet
            mr_stats = self._get_mr_statistics()
            mr_stats.to_excel(writer, sheet_name="MR Statistics", index=False)

            # File Mapping sheet
            file_mapping = self._get_file_mapping()
            file_mapping.to_excel(writer, sheet_name="File Mapping", index=False)

            # Function references sheet
            functions_df = pd.DataFrame(
                sorted(self.unique_functions), columns=["Function Reference"]
            )
            functions_df.to_excel(writer, sheet_name="Functions", index=False)

            # Format all sheets
            for sheet_name in writer.sheets:
                logger.debug(f"Formatting sheet: {sheet_name}")
                worksheet = writer.sheets[sheet_name]

                # Get the appropriate dataframe for this sheet
                if sheet_name == "Raw Data":
                    df_sheet = df
                elif sheet_name == "MR Statistics":
                    df_sheet = mr_stats
                elif sheet_name == "File Mapping":
                    df_sheet = file_mapping
                else:  # Functions sheet
                    df_sheet = functions_df

                # Auto-fit columns based on content
                for idx, col in enumerate(df_sheet.columns):
                    series = df_sheet[col]
                    max_len = max(series.astype(str).apply(len).max(), len(str(col)))
                    # Add some padding
                    max_len = min(max_len + 2, 120)  # Cap maximum width at 120
                    worksheet.set_column(idx, idx, max_len)

                # Special handling for File Mapping sheet
                if sheet_name == "File Mapping":
                    # Apply wrap format to File Locations column (assuming it's the last column)
                    file_locations_col = "D"  # Adjust if column position changes
                    worksheet.set_column(
                        f"{file_locations_col}:{file_locations_col}", 100, wrap_format
                    )

            logger.info(f"Results saved to {output_file}")
            self.console.print(f"[green]Results saved to {output_file}")

    def run(self):
        """
        Run the full extraction process.
        """
        logger.debug("Starting extraction run")
        logger.info("Starting Composition extraction...")
        self.extract_compositions()

        if self.extracted_data:
            logger.info(f"Extracted {len(self.extracted_data)} entries")
            self.console.print(f"[green]Extracted {len(self.extracted_data)} entries")
        else:
            logger.warning("No Composition entries found")
            self.console.print("[yellow]No Composition entries found")


@click.command()
@click.argument("folder_path", type=click.Path(exists=True))
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose output")
@click.option(
    "--output",
    "-o",
    default="composition_extraction.xlsx",
    help="Path to output Excel file",
)
def main(folder_path: str, verbose: bool, output: str):
    """
    Extract details from Crossplane Composition manifests.

    Args:
        folder_path: Path to the folder containing Crossplane manifests
        verbose: Enable verbose logging
        output: Path to output Excel file
    """
    try:
        extractor = CompositionExtractor(folder_path, verbose)
        extractor.run()
        extractor.save_to_excel(output)

    except Exception as e:
        logger.error(f"Error during extraction: {str(e)}")
        raise click.ClickException(str(e))


if __name__ == "__main__":
    main()
