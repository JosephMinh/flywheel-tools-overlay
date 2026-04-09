#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd -- "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source_tools="${repo_root}/tools"
target_tools="${TARGET_TOOLS_DIR:-${HOME}/tools}"

mkdir -p "${target_tools}"
mkdir -p "${HOME}/.local/bin"
mkdir -p "${HOME}/.config"
mkdir -p "${HOME}/.local/state"
mkdir -p "${HOME}/.config/systemd/user"

rsync -a "${source_tools}/" "${target_tools}/"

for tool_dir in "${target_tools}"/*; do
  [ -d "${tool_dir}" ] || continue
  tool_name="$(basename "${tool_dir}")"

  if [ -x "${tool_dir}/${tool_name}" ]; then
    ln -sfn "${tool_dir}/${tool_name}" "${HOME}/.local/bin/${tool_name}"
  fi

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

printf 'Synced tools into %s\n' "${target_tools}"
