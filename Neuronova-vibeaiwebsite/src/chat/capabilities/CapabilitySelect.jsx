import './capability.css'

export default function CapabilitySelect({ commands, value, onChange }) {
  if (commands.length === 0) return null
  return (
    <select
      className="cap-composer-select"
      aria-label="Use capability"
      value={value}
      onChange={(event) => onChange(event.target.value)}
    >
      <option value="">Capabilities · Auto</option>
      {commands.map((item) => (
        <option value={item.capabilityId} key={item.capabilityId}>{item.name}</option>
      ))}
    </select>
  )
}
