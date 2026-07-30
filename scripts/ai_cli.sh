#!/usr/bin/env bash
# ai_cli.sh -- drive VibeAI's interactive CLI from a plain bash shell, so ANY
# AI agent (or CI job) can exercise the real interface and see exactly what a
# human user sees: banner, panels, live agent progress, final answer.
#
# WHY THIS EXISTS
#   The CLI normally uses prompt_toolkit, which needs a real console screen
#   buffer. An AI agent's bash tool runs commands through a pipe (no TTY), so
#   a direct `python main.py` dies with:
#       prompt_toolkit.output.win32.NoConsoleScreenBufferError
#   cli.py already ships a fallback (VIBE_PLAIN_INPUT=1 -> plain input()); this
#   wrapper sets that plus the encoding/width settings that otherwise produce
#   UnicodeEncodeError (cp1252) or 80-col wrapped panels.
#
# USAGE
#   ./scripts/ai_cli.sh "what is a JSON?"
#   ./scripts/ai_cli.sh "first prompt" "second prompt"
#   echo "build me a todo app" | ./scripts/ai_cli.sh
#   ./scripts/ai_cli.sh --timeout 900 "long agent task"
#   ./scripts/ai_cli.sh --width 120 --color "pretty output"
#   ./scripts/ai_cli.sh --check          # config/key check instead of the CLI
#
# EXIT CODES
#   0 ok | 124 timed out | other = interpreter/app failure
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"

TIMEOUT="${VIBE_TIMEOUT:-600}"     # seconds; agent runs are minutes-long
WIDTH="${VIBE_WIDTH:-100}"         # Rich render width (non-TTY defaults to 80)
COLOR=0
MODE="cli"
PROMPTS=()

while [ $# -gt 0 ]; do
  case "$1" in
    --timeout) TIMEOUT="$2"; shift 2 ;;
    --width)   WIDTH="$2";   shift 2 ;;
    # Run against a specific (ideally EMPTY) folder. Strongly recommended for
    # automated testing: the default ./workspace accumulates every past
    # session's leftovers, and the agent demonstrably latches onto stale
    # projects there (observed live 2026-07-23: a fresh landing-page task ran
    # `cd aurora-site && npm run build` on an unrelated leftover project).
    --workspace) export VIBE_WORKSPACE="$2"; mkdir -p "$2"; shift 2 ;;
    --clean-workspace)  # throwaway empty workspace, isolates the run
               _tmpws="${TMPDIR:-/tmp}/vibeai-run-$$"; mkdir -p "$_tmpws"
               export VIBE_WORKSPACE="$_tmpws"; shift ;;
    --color)   COLOR=1;      shift ;;
    --check)   MODE="check"; shift ;;
    -h|--help) sed -n '2,30p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *)         PROMPTS+=("$1"); shift ;;
  esac
done

# ---- interpreter resolution -------------------------------------------------
# Plain `python` on this machine resolves to a 3.14 install WITHOUT the project
# dependencies (rich, loguru, ...). Prefer an interpreter that can actually
# import the app. Override with VIBE_PY="py -3.13" if needed.
pick_python() {
  if [ -n "${VIBE_PY:-}" ]; then echo "$VIBE_PY"; return; fi
  for cand in "py -3.13" "py -3" "python3" "python"; do
    if $cand -c "import rich, loguru" >/dev/null 2>&1; then echo "$cand"; return; fi
  done
  echo ""   # none worked
}
PY="$(pick_python)"
if [ -z "$PY" ]; then
  echo "ERROR: no Python interpreter found with VibeAI's dependencies installed." >&2
  echo "Tried: py -3.13, py -3, python3, python. Set VIBE_PY to override," >&2
  echo "e.g.  VIBE_PY='py -3.13' $0 \"your prompt\"" >&2
  exit 1
fi

# ---- environment ------------------------------------------------------------
export VIBE_PLAIN_INPUT=1          # THE key setting: skip prompt_toolkit
export PYTHONUTF8=1                # avoid cp1252 UnicodeEncodeError on emoji/box chars
export PYTHONIOENCODING=utf-8
export PYTHONUNBUFFERED=1          # stream output as it happens, not at exit
export COLUMNS="$WIDTH"            # Rich picks this up for panel width
if [ "$COLOR" -eq 1 ]; then
  export FORCE_COLOR=1
else
  export NO_COLOR=1                # plain text is easier for an AI to parse
  export TERM=dumb
fi

cd "$ROOT" || exit 1

# ---- config check mode ------------------------------------------------------
if [ "$MODE" = "check" ]; then
  exec timeout "$TIMEOUT" $PY main.py check
fi

# ---- build the input script -------------------------------------------------
# Prompts come from argv, or stdin when none were given. `exit` is ALWAYS
# appended so the REPL terminates instead of blocking forever on EOF.
input_lines() {
  if [ "${#PROMPTS[@]}" -gt 0 ]; then
    printf '%s\n' "${PROMPTS[@]}"
  elif [ ! -t 0 ]; then
    cat
  fi
  printf 'exit\n'
}

# ---- run --------------------------------------------------------------------
input_lines | timeout "$TIMEOUT" $PY main.py cli
rc=$?
if [ $rc -eq 124 ]; then
  echo "" >&2
  echo "[ai_cli] TIMED OUT after ${TIMEOUT}s. Agent tasks can take minutes --" >&2
  echo "[ai_cli] retry with: $0 --timeout 1800 \"<prompt>\"" >&2
fi
exit $rc
