export default function SectionHeader({ index, label, title, children }) {
  return (
    <div className="sec-head" data-reveal>
      <p className="eyebrow">
        <span className="eyebrow-n">{index}</span> / {label}
      </p>
      <h2 className="sec-title">{title}</h2>
      {children && <p className="sec-sub">{children}</p>}
    </div>
  )
}
