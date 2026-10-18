import os.path
import yaml

# --- Start of new Linguist-based loading ---

_linguist_data = None
yaml_path = "../config/languages.yml"

def _load_linguist_data():
    """
    Loads and processes the languages.yml file from the config directory.
    Resolves extension and filename ambiguities by choosing the language with the lowest language_id.
    """
    global _linguist_data
    if _linguist_data is not None:
        return _linguist_data

    try:
        with open(yaml_path, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)
    except FileNotFoundError:
        print(f"Warning: languages.yml not found at {yaml_path}. Using empty definitions.")
        data = {}

    extension_to_lang_info: dict[str, tuple[str, int]] = {}
    filename_to_lang_info: dict[str, tuple[str, int]] = {}
    language_to_type: dict[str, str] = {}

    if data:
        for lang_name, properties in data.items():
            if not isinstance(properties, dict): continue
            lang_type = properties.get('type')
            language_id = properties.get('language_id')

            if not lang_type or language_id is None:
                continue

            language_to_type[lang_name] = lang_type

            # Process extensions
            extensions = properties.get('extensions', [])
            for ext in extensions:
                if not isinstance(ext, str): continue
                clean_ext = ext.lstrip('.').lower()
                if not clean_ext: continue
                if clean_ext not in extension_to_lang_info or language_id < extension_to_lang_info[clean_ext][1]:
                    extension_to_lang_info[clean_ext] = (lang_name, language_id)

            # Process filenames
            filenames = properties.get('filenames', [])
            for filename in filenames:
                if not isinstance(filename, str): continue
                clean_filename = filename.lower()
                if not clean_filename: continue
                if clean_filename not in filename_to_lang_info or language_id < filename_to_lang_info[clean_filename][1]:
                    filename_to_lang_info[clean_filename] = (lang_name, language_id)

    extension_to_language = {ext: info[0] for ext, info in extension_to_lang_info.items()}
    filename_to_language = {fname: info[0] for fname, info in filename_to_lang_info.items()}

    source_extensions, doc_extensions, data_extensions, config_extensions, build_extensions = set(), set(), set(), set(), set()

    for ext, lang_name in extension_to_language.items():
        lang_type = language_to_type.get(lang_name)
        if lang_type == 'programming': source_extensions.add(ext)
        elif lang_type in ('markup', 'prose'): doc_extensions.add(ext)
        elif lang_type == 'data': data_extensions.add(ext)
        elif lang_type == 'configuration': config_extensions.add(ext)
        elif lang_type == 'build': build_extensions.add(ext)

    _linguist_data = {
        'extension_to_language': extension_to_language,
        'filename_to_language': filename_to_language,
        'language_to_type': language_to_type,
        'source_extensions': frozenset(source_extensions),
        'doc_extensions': frozenset(doc_extensions),
        'data_extensions': frozenset(data_extensions),
        'config_extensions': frozenset(config_extensions),
        'build_extensions': frozenset(build_extensions),
    }
    return _linguist_data

def get_linguist_data():
    """Public accessor for the loaded data."""
    return _load_linguist_data()

def find_extension_ambiguities() -> dict[str, list[tuple[str, int]]]:
    """
    Parses the raw linguist data to find extensions used by multiple languages.
    Returns a dictionary mapping extensions to a list of (language_name, language_id) tuples.
    """
    try:
        with open(yaml_path, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)
    except FileNotFoundError:
        return {}

    extension_to_languages: dict[str, list[tuple[str, int]]] = {}
    if data:
        for lang_name, properties in data.items():
            if not isinstance(properties, dict): continue
            extensions = properties.get('extensions', [])
            language_id = properties.get('language_id', -1)
            for ext in extensions:
                if not isinstance(ext, str): continue
                clean_ext = ext.lstrip('.').lower()
                if not clean_ext: continue
                if clean_ext not in extension_to_languages:
                    extension_to_languages[clean_ext] = []
                extension_to_languages[clean_ext].append((lang_name, language_id))
    return {ext: langs for ext, langs in extension_to_languages.items() if len(langs) > 1}

# --- End of new Linguist-based loading ---

# Load data on module import
linguist_data = get_linguist_data()

# Pre-compile all extension sets as module-level constants
_SOURCE_CODE_EXTENSIONS = linguist_data['source_extensions']
_DOC_EXTENSIONS = linguist_data['doc_extensions']
_DATA_EXTENSIONS = linguist_data['data_extensions']
_CONFIG_EXTENSIONS_LINGUIST = linguist_data.get('config_extensions', frozenset())
_BUILD_EXTENSIONS_LINGUIST = linguist_data.get('build_extensions', frozenset())

_FILENAME_TO_LANGUAGE: dict[str, str] = linguist_data['filename_to_language']
_EXTENSION_TO_LANGUAGE: dict[str, str] = linguist_data['extension_to_language']
_LANGUAGE_TO_TYPE: dict[str, str] = linguist_data['language_to_type']

_CONFIG_EXTENSIONS = frozenset({
    "ini", "cfg", "conf", "properties", "env", "json", "yaml", "yml", "toml",
    "xml", "gradle", "pom", "csproj", "sln", "vcproj", "pbxproj", "cmake",
    "mak", "dockerfile", "editorconfig", "prettierrc", "eslintrc", "babelrc",
    "npmrc", "bowerrc", "gitattributes", "gitignore", "gitmodules", "npmignore",
    "dockerignore", "terraform", "tf", "tfvars", "hcl"
}) | _CONFIG_EXTENSIONS_LINGUIST

_IMAGE_EXTENSIONS = frozenset({"png", "jpg", "jpeg", "gif", "bmp", "svg", "webp", "ico", "tiff", "tif", "heic", "avif"})

_BUILD_EXTENSIONS = frozenset({
    "jar", "war", "ear", "dll", "exe", "so", "dylib", "a", "lib", "o", "obj",
    "class", "pyc", "pyo", "pyd", "egg", "whl", "gem", "deb", "rpm", "msi",
    "dmg", "app", "apk", "ipa", "xap", "vsix", "crx", "zip", "tar", "gz", "bz2",
    "xz", "7z", "rar", "iso", "vmdk", "vdi", "vhd", "ova", "ovf", "box",
    "vagrant", "tfstate", "tfstate.backup", "log", "tmp", "temp", "bak", "swp",
    "swo", "swn", "orig", "rej", "patch", "diff", "apkx", "aab", "xcarchive", "nupkg", "cab"
}) | _BUILD_EXTENSIONS_LINGUIST


def _build_extension_mapping() -> dict[str,str]:
    mapping: dict[str,str] = {}
    all_extensions = (
        _SOURCE_CODE_EXTENSIONS | _DOC_EXTENSIONS | _CONFIG_EXTENSIONS |
        _DATA_EXTENSIONS | _IMAGE_EXTENSIONS | _BUILD_EXTENSIONS
    )
    for ext in all_extensions:
        category = "other"
        if ext in _SOURCE_CODE_EXTENSIONS: category = "source_code"
        elif ext in _DOC_EXTENSIONS: category = "documentation"
        elif ext in _BUILD_EXTENSIONS: category = "build"
        elif ext in _CONFIG_EXTENSIONS: category = "configuration"
        elif ext in _DATA_EXTENSIONS: category = "data"
        elif ext in _IMAGE_EXTENSIONS:  category = "image"
        mapping[ext] = category
    return mapping

_EXTENSION_TO_CATEGORIES: dict[str, str] = _build_extension_mapping()

def _get_category_from_lang_type(lang_type: str|None) -> str:
    if lang_type == 'programming': return "source_code"
    if lang_type in ('markup', 'prose'): return "documentation"
    if lang_type == 'data': return "data"
    if lang_type == 'configuration': return "configuration"
    if lang_type == 'build': return "build"
    return "other"


def classify_file_type_language(file_path: str) -> tuple[str, str]:
    file_name = os.path.basename(file_path).lower()

    # 1. Exact filename match
    if file_name in _FILENAME_TO_LANGUAGE:
        lang = _FILENAME_TO_LANGUAGE[file_name]
        lang_type = _LANGUAGE_TO_TYPE.get(lang)
        return _get_category_from_lang_type(lang_type), lang

    # 2. Extension match
    dot_idx = file_path.rfind('.')
    if dot_idx == -1: return "other", "unknown"

    file_extension = file_path[dot_idx+1:].lower()
    category = _EXTENSION_TO_CATEGORIES.get(file_extension, ("other"))
    language = _EXTENSION_TO_LANGUAGE.get(file_extension, "unknown")
    return category, language

if __name__ == "__main__":
    print("--- Finding Extension Ambiguities in languages.yml ---")
    ambiguous_extensions = find_extension_ambiguities()
    if not ambiguous_extensions:
        print("No ambiguous extensions found.")
    else:
        print(f"Found {len(ambiguous_extensions)} ambiguous extensions:")
        for ext, langs in sorted(ambiguous_extensions.items()):
            formatted_langs = [f"{name} (ID: {lang_id})" for name, lang_id in sorted(langs)]
            print(f"  - .{ext}: {', '.join(formatted_langs)}")
    print("--- Finished ---")
