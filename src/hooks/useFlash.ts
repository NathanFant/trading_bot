import { useState, useEffect, useRef } from 'react'

export function useFlash(value: number): 'up' | 'down' | null {
  const [flash, setFlash] = useState<'up' | 'down' | null>(null)
  const prevRef = useRef<number | null>(null)

  useEffect(() => {
    if (prevRef.current !== null && prevRef.current !== value) {
      setFlash(value > prevRef.current ? 'up' : 'down')
      const timer = setTimeout(() => setFlash(null), 900)
      prevRef.current = value
      return () => clearTimeout(timer)
    }
    prevRef.current = value
  }, [value])

  return flash
}
