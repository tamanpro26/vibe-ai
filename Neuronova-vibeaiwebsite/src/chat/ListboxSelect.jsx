import { useState, useRef, useEffect, useId } from 'react'

export default function ListboxSelect({
  options = [],
  value,
  onChange,
  'aria-label': ariaLabel = 'Select option',
  className = '',
}) {
  const [isOpen, setIsOpen] = useState(false)
  const selectedIndex = options.findIndex((opt) => opt.value === value)
  const [focusedIndex, setFocusedIndex] = useState(selectedIndex >= 0 ? selectedIndex : 0)

  const triggerRef = useRef(null)
  const listboxRef = useRef(null)
  const componentId = useId()
  const listboxId = `listbox-panel-${componentId}`

  // Sync focusedIndex with value when closed or changed externally
  useEffect(() => {
    const idx = options.findIndex((opt) => opt.value === value)
    if (idx >= 0) setFocusedIndex(idx)
  }, [value, options])

  // Close panel on click outside
  useEffect(() => {
    if (!isOpen) return
    const handleClickOutside = (e) => {
      if (
        triggerRef.current &&
        !triggerRef.current.contains(e.target) &&
        listboxRef.current &&
        !listboxRef.current.contains(e.target)
      ) {
        closeListbox(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [isOpen])

  const closeListbox = (returnFocus = true) => {
    setIsOpen(false)
    if (returnFocus && triggerRef.current) {
      triggerRef.current.focus()
    }
  }

  const openListbox = () => {
    setIsOpen(true)
    const idx = options.findIndex((opt) => opt.value === value)
    setFocusedIndex(idx >= 0 ? idx : 0)
  }

  const selectOption = (optValue) => {
    onChange?.(optValue)
    closeListbox(true)
  }

  const handleKeyDown = (e) => {
    if (!isOpen) {
      if (['ArrowDown', 'ArrowUp', 'Enter', ' '].includes(e.key)) {
        e.preventDefault()
        openListbox()
      }
      return
    }

    switch (e.key) {
      case 'ArrowDown':
        e.preventDefault()
        setFocusedIndex((prev) => Math.min(options.length - 1, prev + 1))
        break
      case 'ArrowUp':
        e.preventDefault()
        setFocusedIndex((prev) => Math.max(0, prev - 1))
        break
      case 'Home':
        e.preventDefault()
        setFocusedIndex(0)
        break
      case 'End':
        e.preventDefault()
        setFocusedIndex(options.length - 1)
        break
      case 'Enter':
      case ' ':
        e.preventDefault()
        if (options[focusedIndex]) {
          selectOption(options[focusedIndex].value)
        }
        break
      case 'Escape':
      case 'Tab':
        e.preventDefault()
        closeListbox(true)
        break
      default:
        break
    }
  }

  const currentOption = options.find((opt) => opt.value === value) || options[0]

  return (
    <div className={`custom-listbox ${className}`.trim()}>
      <button
        ref={triggerRef}
        type="button"
        className="listbox-trigger"
        role="combobox"
        aria-haspopup="listbox"
        aria-expanded={isOpen}
        aria-controls={listboxId}
        aria-label={ariaLabel}
        aria-activedescendant={
          isOpen && options[focusedIndex] ? `opt-${componentId}-${options[focusedIndex].value}` : undefined
        }
        onClick={() => (isOpen ? closeListbox(true) : openListbox())}
        onKeyDown={handleKeyDown}
      >
        <span className="listbox-value">{currentOption?.label}</span>
        <svg
          className={`listbox-chevron${isOpen ? ' is-open' : ''}`}
          viewBox="0 0 16 16"
          fill="none"
          aria-hidden="true"
        >
          <path
            d="M4 6L8 10L12 6"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </button>

      {isOpen && (
        <ul
          ref={listboxRef}
          id={listboxId}
          className="listbox-panel"
          role="listbox"
          tabIndex={-1}
          aria-label={ariaLabel}
          onKeyDown={handleKeyDown}
        >
          {options.map((opt, idx) => {
            const isSelected = opt.value === value
            const isFocused = idx === focusedIndex
            const optionId = `opt-${componentId}-${opt.value}`
            return (
              <li
                key={opt.value}
                id={optionId}
                role="option"
                aria-selected={isSelected}
                className={`listbox-option${isSelected ? ' is-selected' : ''}${isFocused ? ' is-focused' : ''}`}
                onClick={() => selectOption(opt.value)}
                onMouseEnter={() => setFocusedIndex(idx)}
              >
                <span className="listbox-option-label">{opt.label}</span>
                {isSelected && <span className="listbox-check" aria-hidden="true">✓</span>}
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}
