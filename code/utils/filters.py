# code/aie/analysis/filters.py
import re
import os
from pathlib import Path


class FileFilter:
    """Handles filtering of auto-generated and unwanted files"""

    def __init__(self):
        self.excluded_extensions = {
            # Generated code
            '.min.js', '.min.css', '.bundle.js', '.chunk.js', '.vendor.js',
            '.d.ts', '.pb.go', '.pb.js', '.pb.cc', '.pb.h',
            '.pb2.py', '.pb2_grpc.py', '.thrift.py', '.thrift.go', '.thrift.js',
            # Build artifacts
            '.o', '.obj', '.exe', '.dll', '.so', '.dylib', '.a', '.lib',
            '.class', '.jar', '.war', '.ear', '.pyc', '.pyo', '.pyd',
            '.wasm', '.bc', '.ll', '.s', '.asm', '.beam', '.cmi', '.cmo', '.cmx',
            '.hi', '.dyn_hi', '.dyn_o',
            # Assets
            '.png', '.jpg', '.jpeg', '.gif', '.bmp', '.svg', '.ico', '.webp',
            '.mp4', '.avi', '.mov', '.wmv', '.flv', '.mp3', '.wav', '.ogg',
            '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx',
            '.zip', '.tar', '.gz', '.rar', '.7z', '.bz2', '.xz',
            '.ttf', '.otf', '.woff', '.woff2', '.eot',
            # Lock files
            '.lock', '.sum', '.resolved'
        }
        combined_pattern = "|".join(f"(?:{p})" for p in self.excluded_extensions)
        self.excluded_ext = re.compile(combined_pattern)


        self.excluded_directories = {
            'node_modules', 'vendor', 'third_party', 'external',
            'build', 'dist', 'out', 'target', 'bin', 'obj', '.build',
            'generated', 'gen', 'auto', 'codegen',
            '__pycache__', '.pytest_cache', '.coverage', '.tox',
            '.git', '.svn', '.hg', '.bzr',
            '.idea', '.vscode', '.vs', '.settings',
            'bower_components', 'jspm_packages',
            'Pods', 'DerivedData', 'site-packages',
            'cmake-build-debug', 'cmake-build-release', '.cmake',
            'META-INF', 'WEB-INF', 'classes',
            '.bundle', '.gem', 'pkg'
        }
        combined_pattern = "|".join(f"(?:{p})" for p in self.excluded_directories)
        self.excluded_dir = re.compile(combined_pattern)


        self.excluded_patterns = [
            # --- Generic generated / auto files ---
            r'.*\.generated\..*',
            r'.*\.gen\..*',
            r'.*\.auto\..*',
            r'.*_pb2\.py$',                 # Python protobuf
            r'.*_pb2_grpc\.py$',            # Python gRPC
            r'.*\.pb\.go$',                 # Go protobuf
            r'.*\.g\.cs$',                  # C# generated partials
            r'.*\.designer\.cs$',           # C# WinForms / XAML designer
            r'.*\.resx$',                   # .NET resource files
            r'.*\.moc\..*',                 # Qt MOC generated files
            r'.*\.ui$',                     # Qt UI XML files
            r'.*\.g\.dart$',                # Flutter generated code
            r'.*\.freezed\.dart$',          # Dart Freezed models
            r'.*\.schema\.json$',           # Generated JSON schema
            r'.*\.swagger\.json$',          # Swagger/OpenAPI generated specs
            r'.*\.openapi\.json$',

            # --- Dependency lock / resolver files ---
            r'package-lock\.json$',         # npm
            r'pnpm-lock\.yaml$',            # pnpm
            r'yarn\.lock$',                 # yarn
            r'Gemfile\.lock$',              # Ruby
            r'composer\.lock$',             # PHP
            r'poetry\.lock$',               # Python Poetry
            r'Pipfile\.lock$',              # Pipenv
            r'Cargo\.lock$',                # Rust
            r'go\.sum$',                    # Go
            r'packages\.lock\.json$',       # .NET NuGet
            r'Package\.resolved$',          # Swift
            r'mix\.lock$',                  # Elixir

            # --- Build / configuration artifacts ---
            r'Makefile\.in$',               # autotools
            r'configure$',                  # configure scripts
            r'config\.h\.in$',              # C/C++ generated headers
            r'CMakeCache\.txt$',            # CMake cache
            r'compile_commands\.json$',     # Compilation database
            r'autoreconf\.ac$',             # autoconf files
            r'autogen\.sh$',                # autotools generated scripts

            # --- Platform / IDE generated files ---
            r'R\.java$',                    # Android resource class
            r'BuildConfig\.java$',          # Android build config
            r'.*\.xcassets/.*',             # iOS/macOS asset catalogs
            r'.*\.storyboard$',             # iOS UI files
            r'.*\.xib$',                    # iOS Interface Builder files
            r'.*\.pbxproj$',                # Xcode project
            r'.*\.xcworkspace.*',           # Xcode workspace
            r'.*\.iml$',                    # IntelliJ IDEA module files
            r'.*\.csproj$',                 # Visual Studio C# project
            r'.*\.vbproj$',                 # Visual Studio VB project
            r'.*\.vcxproj.*',               # Visual Studio C++ project
        ]
        combined_pattern = "|".join(f"(?:{p})" for p in self.excluded_patterns)
        self.excluded_re = re.compile(combined_pattern)


    def should_include_file(self, file_path: str) -> bool:
        """Determine if a file should be included in analysis"""
        if not file_path:
            return False

        # Check extension
        _, ext = os.path.splitext(file_path)
        if ext.lower() in self.excluded_extensions:
            return False

        # Check directory components
        path_parts = file_path.split('/')
        if any(part in self.excluded_directories for part in path_parts):
            return False

        # Check patterns
        for pattern in self.excluded_patterns:
            if re.match(pattern, file_path, re.IGNORECASE):
                return False

        return True

    def should_include(self, filepath: str) -> bool:
        if self.excluded_re.search(Path(filepath).name):
            return False
        return True




class CommitFilter:
    """Handles filtering of unwanted commits"""

    def __init__(self):
        self.bot_patterns = [
            r'.*bot.*@.*',
            r'.*ci.*@.*',
            r'noreply@github\.com',
            r'.*automated.*@.*',
            r'.*dependabot.*@.*',
            r'.*renovate.*@.*'
        ]
        self.bot_patterns = []  # done in another way

        self.message_patterns = [
            r'^Revert\s+',
            r'^Merge\s+',
            r'^Update dependencies',
            r'^Bump version',
            r'^Auto-format',
            r'^Automatic\s+',
            r'^Release v\d+\.\d+\.\d+',
            r'^Version bump',
            r'^Merge pull request',
            r'^Merge branch',
            r'^\[skip ci\]',
            r'^\[ci skip\]',
            r'^Update\s+.*\.lock$',
            r'^Regenerate\s+',
            r'^Auto-update\s+'
        ]

    def should_include_commit(self, commit_data: dict) -> bool:
        """Determine if a commit should be included in analysis"""
        # Skip merge commits
        if len(commit_data['parents']) > 1:
            return False

        # Check author email patterns
        author_email = commit_data['email'].lower()
        for pattern in self.bot_patterns:
            if re.match(pattern, author_email, re.IGNORECASE):
                return False

        # Check commit message patterns
        message = commit_data['message'].strip()
        for pattern in self.message_patterns:
            if re.match(pattern, message, re.IGNORECASE):
                return False

        return True
