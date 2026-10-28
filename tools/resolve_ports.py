"""Print only resolved listener ports for the development launchers; never source .env as shell."""

import os
from pathlib import Path

from oversight.settings import read_env_file, resolve_ports


def main() -> None:
    port, vite_port = resolve_ports(
        read_env_file(Path.cwd() / ".env") | dict(os.environ), development=True
    )
    print(port, vite_port)


if __name__ == "__main__":
    main()
