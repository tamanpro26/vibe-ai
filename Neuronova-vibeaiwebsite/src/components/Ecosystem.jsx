import SectionHeader from './SectionHeader.jsx'

const SURFACES = [
  {
    id: '01',
    name: 'AI Workspace',
    type: 'Product',
    use: 'Authenticated chat, projects, conversation history, attachments, and downloadable coding results.',
    proof: 'web chat + project routes',
    featured: true,
  },
  {
    id: '02',
    name: 'Coding Agent',
    type: 'Execution',
    use: 'Maps a workspace, plans changes, edits files, runs commands, corrects failures, and verifies the result.',
    proof: 'core/agent_loop.py',
  },
  {
    id: '03',
    name: 'VibeMind',
    type: 'Desktop',
    use: 'A separate assistant for persistent conversations, tasks, app commands, and delegated coding work.',
    proof: 'vibemind/',
  },
  {
    id: '04',
    name: 'Video Observer',
    type: 'Multimodal',
    use: 'Compresses video into temporal frame summaries and selects useful visual evidence for model analysis.',
    proof: 'video_observer/',
  },
  {
    id: '05',
    name: 'VS Code Extension',
    type: 'Developer',
    use: 'Brings @vibeai chat and selected-code actions into the editor through the local API.',
    proof: 'vscode-extension/',
  },
  {
    id: '06',
    name: 'Sensor Alerts',
    type: 'Applied AI',
    use: 'Turns ESP32 temperature events into bounded buzzer, LED, reasoning, and push-notification flows.',
    proof: 'hardware/ + /api/sensor',
  },
]

export default function Ecosystem() {
  return (
    <section className="section" id="ecosystem">
      <div className="container">
        <SectionHeader index="06" label="PRODUCT SURFACES" title="One orchestration core. Practical interfaces.">
          VibeAI is more than a routing diagram. The same system reaches the browser, terminal,
          editor, desktop, video workflows, and a focused hardware alert application.
        </SectionHeader>
        <div className="ecosystem-list">
          {SURFACES.map((surface, index) => (
            <article
              className={`ecosystem-row${surface.featured ? ' is-featured' : ''}`}
              key={surface.name}
              data-reveal
              style={{ transitionDelay: `${index * 0.055}s` }}
            >
              <span className="ecosystem-id">{surface.id}</span>
              <div className="ecosystem-name-block">
                <span className="ecosystem-type">{surface.type}</span>
                <h3>{surface.name}</h3>
              </div>
              <p>{surface.use}</p>
              <code>{surface.proof}</code>
            </article>
          ))}
        </div>
      </div>
    </section>
  )
}
