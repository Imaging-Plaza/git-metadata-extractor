{
  description = "git-metadata-extractor — dev shell (Python 3.12, matching CI)";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { nixpkgs, flake-utils, ... }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = nixpkgs.legacyPackages.${system};

        # CI pins 3.12 (.github/workflows/ci.yml), and the pinned native deps
        # (faiss-cpu, duckdb, tiktoken) have the widest cp312 wheel coverage.
        python = pkgs.python312;

        # manylinux wheels are not patchelf'd, so they need these at runtime.
        # nix-ld is already enabled on this host, but setting LD_LIBRARY_PATH
        # explicitly keeps the shell working with or without it.
        nativeLibs = with pkgs; [
          stdenv.cc.cc.lib # libstdc++, libgcc_s, libgomp -> faiss-cpu, duckdb
          zlib # libz -> numpy, duckdb
          openssl # some wheels link libssl directly
        ];
      in
      {
        devShells.default = pkgs.mkShell {
          packages = with pkgs; [
            python
            uv # the venv in-tree was already created by uv
            just # the justfile is the documented interface
            jq # scripts/v2/batch_extract.sh needs it (7 call sites)
            curl # batch_extract.sh posts/polls with curl
            git # the open-pulse-sources dep resolves over git+https
          ];

          env = {
            LD_LIBRARY_PATH = pkgs.lib.makeLibraryPath nativeLibs;
            # Never let uv fetch its own interpreter; the venv below is built
            # from the nix python so it matches CI.
            #
            # Deliberately NOT setting UV_PYTHON: it makes `uv pip install`
            # target that interpreter directly, and the nix-store python is
            # read-only, so the install fails with "consider creating a
            # virtual environment". The venv is selected by activating it.
            UV_PYTHON_DOWNLOADS = "never";
          };

          shellHook = ''
            # Create the venv from the nix 3.12 interpreter if it is missing or
            # built against another version (the in-tree one was 3.13).
            if [ -f .venv/pyvenv.cfg ] && ! grep -q '^version_info = 3\.12' .venv/pyvenv.cfg; then
              echo "note: .venv is Python $(sed -n 's/^version_info = //p' .venv/pyvenv.cfg); rebuilding on 3.12 (CI's version)"
              rm -rf .venv
            fi
            if [ ! -d .venv ]; then
              uv venv --python "${python}/bin/python3.12" >/dev/null
            fi

            # Activate it, so `uv pip install` and `python3` both resolve here.
            export VIRTUAL_ENV="$PWD/.venv"
            export PATH="$PWD/.venv/bin:$PATH"

            echo "git-metadata-extractor dev shell — Python $(python3 -V 2>&1 | cut -d' ' -f2)"
            echo "  just install-dev     install with dev extras"
            echo "  just check           lint + type-check"
            echo "  just test-full       full deterministic test run"
            echo "  just serve-dev       uvicorn on port ''${PORT:-1234}"
            echo
          '';
        };
      });
}
