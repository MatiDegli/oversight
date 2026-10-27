# Sourced by make dev/replay. Resolve once using the backend's parser and export to both processes.
# A failure in the resolver stops the launcher before either listener starts.
ports=$(uv run --frozen python tools/resolve_ports.py)
read -r OVERSIGHT_PORT OVERSIGHT_VITE_PORT <<< "$ports"
export OVERSIGHT_PORT OVERSIGHT_VITE_PORT
