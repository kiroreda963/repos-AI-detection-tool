"""
Shared LaTeX generation utilities for report generation.

Consolidates duplicated functionality across multiple report scripts.
"""

import re
from typing import Dict, Any


def build_sanitized_caption(template: str, counts: Dict[str, Any]) -> str:
    """
    Build a caption from a template and counts dict.
    
    Validates that all \\command references in the template exist as keys in counts.
    Sanitizes the caption for LaTeX while preserving \\command references.
    
    Args:
        template: Caption template with \\commandName references
        counts: Dictionary of values to use in caption
        
    Returns:
        Sanitized caption text
        
    Raises:
        ValueError: If any required keys are missing from counts dict
    """
    # Extract all \command patterns from template
    command_pattern = r'\\([a-zA-Z]+(?:[a-zA-Z0-9]*)?)'
    commands_in_template = re.findall(command_pattern, template)
    
    # Validate all commands exist in counts
    missing_keys = []
    for cmd_name in commands_in_template:
        if cmd_name not in counts:
            missing_keys.append(cmd_name)
    
    if missing_keys:
        raise ValueError(f"Missing required keys in counts dict: {missing_keys}")
    
    # Replace commands with placeholders to protect them (use no special chars)
    sanitized = template
    placeholders = {}
    for i, cmd_name in enumerate(set(commands_in_template)):
        placeholder = f"CMDPLACEHOLDER{i}CMDEND"
        placeholders[placeholder] = f"\\{cmd_name}"
        sanitized = sanitized.replace(f"\\{cmd_name}", placeholder)
    
    # Escape LaTeX special characters
    special_chars = [
        ("_", "\\_"),
        ("&", "\\&"),
        ("%", "\\%"),
        ("#", "\\#"),
        ("$", "\\$"),
    ]
    for char, escaped in special_chars:
        sanitized = sanitized.replace(char, escaped)
    
    # Restore commands
    for placeholder, cmd in placeholders.items():
        sanitized = sanitized.replace(placeholder, cmd)
    
    return sanitized


def number_to_word(n: int) -> str:
    """
    Convert number to word for LaTeX command names.
    
    Handles 0-99 with proper names, then digit-by-digit for >= 100.
    
    Examples:
        0 -> "zero"
        5 -> "five"
        10 -> "ten"
        11 -> "eleven"
        15 -> "fifteen"
        20 -> "twenty"
        25 -> "twentyfive"
        30 -> "thirty"
        42 -> "fortytwo"
        99 -> "ninetynine"
        100 -> "onezerozero"
        1234 -> "onetwothreefour"
    """
    ones = [
        "zero", "one", "two", "three", "four", "five",
        "six", "seven", "eight", "nine", "ten", "eleven",
        "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
        "seventeen", "eighteen", "nineteen"
    ]
    
    tens = [
        "", "", "twenty", "thirty", "forty", "fifty",
        "sixty", "seventy", "eighty", "ninety"
    ]
    
    # Handle 0-19
    if n < 20:
        return ones[n]
    
    # Handle 20-99
    if n < 100:
        tens_digit = n // 10
        ones_digit = n % 10
        if ones_digit == 0:
            return tens[tens_digit]
        else:
            return tens[tens_digit] + ones[ones_digit]
    
    # For numbers >= 100, convert digit by digit
    return "".join(ones[int(d)] for d in str(n))


def effect_size_interpretation(effect_size: float) -> str:
    """Interpret chi-squared effect size."""
    if effect_size < 0.1:
        return "Negligible"
    elif effect_size < 0.3:
        return "Small"
    elif effect_size < 0.5:
        return "Medium"
    else:
        return "Large"


def sanitize_latex_command_name(name: str) -> str:
    """
    Convert a string to a valid LaTeX command name (letters only).
    
    LaTeX command names can only contain letters a-z (case-insensitive).
    This function:
    - Replaces comparison operators (>, <, =) with words (gt, lt, eq)
    - Converts any digits to words using number_to_word()
    - Removes spaces, underscores, hyphens
    
    Examples:
        "my_metric" -> "mymetric"
        "foo bar" -> "foobar"
        "x>5" -> "xgtfive"
        "category<10" -> "categoryltonezero"
        "metric2025" -> "metrictwozerotwofive"
    """
    # Replace comparison operators
    name = name.replace(">", "gt").replace("<", "lt").replace("=", "eq")
    
    # Convert any digits to words
    result = ""
    for char in name:
        if char.isdigit():
            result += number_to_word(int(char))
        else:
            result += char
    
    # Remove spaces, underscores, hyphens
    result = result.replace(" ", "").replace("_", "").replace("-", "")
    
    return result


def generate_sparkline_command(
    values: list[float],
    command_name: str,
    scale_factor: float = 1.0,
    use_tikz_bars: bool = True,
) -> str:
    """
    Generate a LaTeX sparkline command definition.
    
    Parameters
    ----------
    values : list[float]
        Numeric values to include in sparkline
    command_name : str
        Full command name (including backslash), e.g., "\\mysparkline"
    scale_factor : float
        Scale factor to apply to values (default 1.0)
    use_tikz_bars : bool
        If True, use sparklineTikzBars; if False, use sparklineTikzLine
    
    Returns
    -------
    str
        LaTeX command definition
    """
    scaled_values = [f"{v * scale_factor:.2f}" for v in values]
    coords = ", ".join(scaled_values)
    
    if use_tikz_bars:
        return f"\\newcommand{{{command_name}}}{{\\sparklineTikzBars{{{coords}}}}}"
    else:
        return f"\\newcommand{{{command_name}}}{{\\sparklineTikzLine{{{coords}}}}}"


def generate_nested_sparkline_commands(
    data_dict: dict,
    name_builder,
    scale_factor: float = 2.0,
) -> list[str]:
    """
    Generate LaTeX sparkline commands from nested dict structures.
    
    Recursively walks a nested dictionary of numeric values and generates
    sparkline commands for each leaf list/array.
    
    Parameters
    ----------
    data_dict : dict
        Nested dictionary where leaf values are lists of numbers.
        Can be any depth: {a: [values]} or {a: {b: [values]}} or {a: {b: {c: [values]}}}
    name_builder : callable
        Function that takes a list of keys (the path to this data) and returns
        the full LaTeX command name (without backslash). Should handle the nesting
        structure appropriately.
        Example: lambda keys: f"metric{keys[0]}Sparkline" for flat dict
                 lambda keys: f"mask{keys[0]}decile{keys[1]}Sparkline" for nested dict
    scale_factor : float
        Scale factor to apply to values (default 2.0)
    
    Returns
    -------
    list[str]
        List of LaTeX command definitions
    """
    commands = []
    
    def recurse(node, path):
        if isinstance(node, dict):
            # Recurse into nested dict
            for key, value in node.items():
                recurse(value, path + [key])
        elif isinstance(node, (list, tuple)):
            # Leaf node: generate sparkline command
            if len(node) > 0:
                # Scale values
                scaled_values = [f"{v * scale_factor:.2f}" for v in node]
                coords = ", ".join(scaled_values)
                
                # Build command name from path
                cmd_name = name_builder(path)
                cmd_def = f"\\newcommand{{\\{cmd_name}}}{{\\sparklineTikzBars{{{coords}}}}}"
                commands.append(cmd_def)
    
    recurse(data_dict, [])
    return commands


def format_chi2_results_as_latex(chi2_results: list[dict]) -> str:
    """
    Format chi-squared test results as LaTeX table.
    
    Parameters
    ----------
    chi2_results : list[dict]
        List of dicts with keys: metric/mask_name, chi2, p_value, effect_size, interpretation
    
    Returns
    -------
    str
        LaTeX table code
    """
    latex = "% Chi-Squared Test Results\n\n"
    latex += "\\begin{table}[ht]\n"
    latex += "\\centering\n"
    latex += "\\caption{Chi-squared goodness-of-fit tests}\n"
    latex += "\\label{tab:chi2_results}\n"
    latex += "\\begin{tabular}{lrrrl}\n"
    latex += "\\toprule\n"
    
    # Determine column header based on result keys
    if chi2_results and "mask_name" in chi2_results[0]:
        header_label = "Category"
    else:
        header_label = "Metric"
    
    latex += f"\\textbf{{{header_label}}} & \\textbf{{Chi-squared}} & \\textbf{{p-value}} & \\textbf{{Effect Size}} & \\textbf{{Interpretation}} \\\\\n"
    latex += "\\midrule\n"

    for result in chi2_results:
        # Handle both metric and mask_name keys
        if "mask_name" in result:
            name = result["mask_name"]
        else:
            name = result["metric"].replace("_", " ").title()
        
        chi2 = f"{result['chi2']:.4f}"
        p_value = f"{result['p_value']:.6f}"
        effect_size = f"{result['effect_size']:.4f}"
        interpretation = result["interpretation"]

        latex += f"{name} & {chi2} & {p_value} & {effect_size} & {interpretation} \\\\\n"

    latex += "\\bottomrule\n"
    latex += "\\end{tabular}\n"
    latex += "\\end{table}\n"

    return latex
