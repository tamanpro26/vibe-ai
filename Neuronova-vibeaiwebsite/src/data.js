export const PROVIDERS = [
  { name: 'Google Gemini', color: '#5ec8ff' },
  { name: 'Groq', color: '#ff9f1c' },
  { name: 'Cerebras', color: '#c77dff' },
  { name: 'OpenRouter', color: '#5cf2b0' },
  { name: 'NVIDIA NIM', color: '#8fe000' },
  { name: 'Mistral', color: '#ff8a3d' },
  { name: 'Z.AI', color: '#ff5da2' },
  { name: 'Pollinations', color: '#ffd23f' },
  { name: 'Ollama', color: '#9ad1ff' },
  { name: 'Anthropic', color: '#ff7a59', optional: true },
]

export const COUNCIL_STAGES = [
  {
    n: '01',
    name: 'Plan',
    desc: 'The task is decomposed and a contract for the answer is set before any model writes a word.',
  },
  {
    n: '02',
    name: 'Draft',
    desc: 'A free-tier model produces the first candidate answer against the plan.',
  },
  {
    n: '03',
    name: 'Critique',
    desc: 'A different model attacks the draft — gaps, errors, and weak reasoning get flagged.',
  },
  {
    n: '04',
    name: 'Refine',
    desc: 'The draft is rewritten to answer every point the critique raised.',
  },
  {
    n: '05',
    name: 'Synthesize',
    desc: 'The final answer is merged and checked back against the original contract.',
  },
]

export const TEAMS = [
  {
    name: 'Manager Council',
    color: 'var(--c-manager)',
    desc: '5-stage reasoning pipeline. Multiple free models collaborate on one answer.',
  },
  {
    name: 'Code',
    color: 'var(--c-code)',
    desc: 'Autonomous coding agent — writes real files, runs shell, verifies itself.',
  },
  {
    name: 'Brain',
    color: 'var(--c-brain)',
    desc: 'Deep reasoning team for analysis, math, and multi-step logic.',
  },
  {
    name: 'Vision',
    color: 'var(--c-vision)',
    desc: 'Image understanding routed to vision-capable free models.',
  },
  {
    name: 'Design',
    color: 'var(--c-design)',
    desc: 'Generation and design tasks — layouts, assets, image output.',
  },
  {
    name: 'Router',
    color: 'var(--c-router)',
    desc: 'Classifies intent and dispatches every request to the right team.',
  },
]

export const OPS_LINES = [
  { tag: 'ROUTER', color: 'var(--c-router)', text: 'intent classified: code — dispatch team.code' },
  { tag: 'AGENT', color: 'var(--c-code)', text: 'writing src/parser.py · running pytest -q' },
  { tag: 'VERIFY', color: 'var(--mint)', text: 'battery pass — 0 broken imports, 0 stub blocks' },
  { tag: 'CB', color: 'var(--amber)', text: 'groq returned 429 — circuit OPEN for 60s' },
  { tag: 'CB', color: 'var(--mint)', text: 'failover → openrouter · 0 requests dropped' },
  { tag: 'COUNCIL', color: 'var(--cyan)', text: 'stage 3/5 critique — attacking the draft' },
  { tag: 'LEADER', color: 'var(--c-vision)', text: 'team.brain output approved on review' },
  { tag: 'CRITIC', color: 'var(--rose)', text: 'reflexion: placeholder stub found — patching' },
  { tag: 'VERIFY', color: 'var(--mint)', text: 're-run pass — task reported done' },
  { tag: 'CEO', color: 'var(--c-brain)', text: 'oversight report generated from ops logs' },
]
