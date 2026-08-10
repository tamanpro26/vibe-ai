import { useContext } from 'react'
import LandingSoundContext from './landingSoundContext.js'

export default function useLandingSound() {
  const value = useContext(LandingSoundContext)
  if (!value) throw new Error('useLandingSound must be used inside LandingSoundProvider')
  return value
}
