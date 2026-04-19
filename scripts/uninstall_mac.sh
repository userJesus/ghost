#!/usr/bin/env bash
# ============================================================
#  Ghost — macOS uninstaller
#
#  Ships inside the .dmg. When run, it:
#    1. Removes /Applications/Ghost.app
#    2. Asks whether to delete user data (~/.ghost) and shows the path
#    3. Asks whether to also uninstall the BlackHole 2ch driver
#
#  Data path: ~/.ghost
# ============================================================
set -euo pipefail

APP="/Applications/Ghost.app"
DATA="${HOME}/.ghost"

echo "Ghost — desinstalador"
echo "======================"
echo

# -----------------------------------------------
# 1) App removal
# -----------------------------------------------
if [ -d "${APP}" ]; then
    echo "Removendo o app de ${APP}..."
    rm -rf "${APP}"
    echo "  [ok]"
else
    echo "O app não foi encontrado em ${APP}."
fi

# -----------------------------------------------
# 2) User data
# -----------------------------------------------
echo
if [ -d "${DATA}" ]; then
    echo "Seus dados do Ghost (logs, configurações, histórico de conversas, chave da OpenAI) estão em:"
    echo "  ${DATA}"
    echo
    echo "Deseja excluir também esses dados?"
    echo "  s) Sim — remove tudo (limpeza completa)."
    echo "  n) Não — mantém os dados (padrão)."
    echo
    read -r -p "Opção [n]: " choice
    case "${choice}" in
        [sSyY]*)
            rm -rf "${DATA}"
            echo "Dados removidos."
            ;;
        *)
            echo "Dados mantidos em ${DATA}."
            ;;
    esac
else
    echo "Nenhum dado de usuário encontrado em ${DATA}."
fi

# -----------------------------------------------
# 3) BlackHole driver (optional)
# -----------------------------------------------
BH_PLIST="/Library/Audio/Plug-Ins/HAL/BlackHole2ch.driver"
if [ -d "${BH_PLIST}" ]; then
    echo
    echo "O driver BlackHole 2ch está instalado. Ele é usado pelo Ghost para capturar"
    echo "o áudio de reuniões. Se você só usou o Ghost, pode removê-lo. Se outros apps"
    echo "(ex.: Loopback, gravadores) dependem dele, mantenha."
    echo
    read -r -p "Remover BlackHole 2ch? [n]: " bh_choice
    case "${bh_choice}" in
        [sSyY]*)
            echo "Removendo driver BlackHole (requer senha de admin)..."
            sudo rm -rf "${BH_PLIST}"
            sudo launchctl kickstart -k system/com.apple.audio.coreaudiod 2>/dev/null || true
            echo "BlackHole removido."
            ;;
        *)
            echo "BlackHole mantido."
            ;;
    esac
fi

echo
echo "Desinstalação concluída."
