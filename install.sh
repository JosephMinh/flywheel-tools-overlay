#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd -- "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source_tools="${repo_root}/tools"

mkdir -p "${HOME}/.local/bin"
mkdir -p "${HOME}/bin"
mkdir -p "${HOME}/.config"
mkdir -p "${HOME}/.local/state"
mkdir -p "${HOME}/.config/systemd/user"

for tool_dir in "${source_tools}"/*; do
  [ -d "${tool_dir}" ] || continue
  tool_name="$(basename "${tool_dir}")"

  while IFS= read -r executable; do
    exec_name="$(basename "${executable}")"
    ln -sfn "${executable}" "${HOME}/.local/bin/${exec_name}"
    ln -sfn "${executable}" "${HOME}/bin/${exec_name}"
  done < <(find "${tool_dir}" -maxdepth 1 -mindepth 1 -type f -perm -u+x | sort)

  if [ -d "${tool_dir}/config" ]; then
    ln -sfn "${tool_dir}/config" "${HOME}/.config/${tool_name}"
  fi

  if [ -d "${tool_dir}/state" ]; then
    ln -sfn "${tool_dir}/state" "${HOME}/.local/state/${tool_name}"
  fi

  if [ -f "${tool_dir}/systemd/${tool_name}.service" ]; then
    ln -sfn "${tool_dir}/systemd/${tool_name}.service" "${HOME}/.config/systemd/user/${tool_name}.service"
  fi
done

if command -v systemctl >/dev/null 2>&1; then
  systemctl --user daemon-reload >/dev/null 2>&1 || true
fi

printf 'Linked tool launchers, config, state, and services from %s\n' "${source_tools}"
