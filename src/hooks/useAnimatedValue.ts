import { useState, useEffect, useRef } from 'react'

export function useAnimatedValue(target: number, duration = 700): number {
  const [value, setValue] = useState(target)
  const fromRef = useRef(target)
  const rafRef = useRef(0)
  const seenRef = useRef(false)

  useEffect(() => {
    if (!seenRef.current) {
      seenRef.current = true
      fromRef.current = target
      setValue(target)
      return
    }

    const from = fromRef.current
    const startTime = performance.now()
    cancelAnimationFrame(rafRef.current)

    const tick = (now: number) => {
      const t = Math.min((now - startTime) / duration, 1)
      const eased = 1 - Math.pow(1 - t, 3)
      setValue(from + (target - from) * eased)
      if (t < 1) rafRef.current = requestAnimationFrame(tick)
      else fromRef.current = target
    }

    rafRef.current = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(rafRef.current)
  }, [target, duration])

  return value
}
