import { Activity, CalendarRange, GitCompare, Globe2, Layers, MapPin, Search, Thermometer } from 'lucide-react'
import { ru } from './ru'

export const researchModes = [
  { value: 'single', slug: 'point', label: ru.app.modes.single, Icon: MapPin, title: 'Климат в выбранной точке', hint: 'Выберите место, показатель и период, чтобы оценить многолетний климатический тренд.' },
  { value: 'compare', slug: 'compare', label: ru.app.modes.compare, Icon: GitCompare, title: 'Сравнение двух точек', hint: 'Сопоставьте годовые ряды двух мест за общий период.' },
  { value: 'profile', slug: 'profile', label: 'Климатический профиль', Icon: Layers, title: 'Климатический профиль', hint: 'Сводка температуры, осадков, сезонных изменений и экстремумов для одной точки.' },
  { value: 'anomalies', slug: 'anomalies', label: ru.app.modes.anomalies, Icon: Activity, title: ru.app.modes.anomalies, hint: 'Сопоставьте каждый год со средним выбранного базового периода.' },
  { value: 'seasonal', slug: 'seasonal', label: ru.app.modes.seasonal, Icon: CalendarRange, title: ru.app.modes.seasonal, hint: 'Исследуйте изменения по сезонам или в одном календарном месяце.' },
  { value: 'extremes', slug: 'extremes', label: 'Погодные экстремумы', Icon: Thermometer, title: 'Погодные экстремумы', hint: 'Оцените частоту пороговых событий и длительность волн жары.' },
  { value: 'regional-map', slug: 'trend-map', label: 'Карта климатических трендов', Icon: Globe2, title: 'Пространственные тренды', hint: 'Изучите направление и выраженность климатических трендов на карте мира.' },
  { value: 'detective', slug: 'detective', label: ru.app.modes.detective, Icon: Search, title: 'Климатический детектив', hint: 'Выберите вопрос и проведите исследование по шагам.' },
] as const

export type ResearchMode = typeof researchModes[number]['value']
export type SitePage = 'home' | 'catalog' | ResearchMode

const base = import.meta.env.BASE_URL

export function pageUrl(page: SitePage): string {
  if (page === 'home') return base
  if (page === 'catalog') return `${base}research`
  const mode = researchModes.find(entry => entry.value === page)!
  return `${base}research/${mode.slug}`
}

export function pageFromPath(path: string): SitePage | 'not-found' {
  const pathname = path.replace(/\/+$/, '')
  if (pathname === pageUrl('home').replace(/\/+$/, '')) return 'home'
  if (pathname === pageUrl('catalog')) return 'catalog'
  return researchModes.find(entry => pageUrl(entry.value) === pathname)?.value ?? 'not-found'
}

export function isResearchMode(page: SitePage | 'not-found'): page is ResearchMode {
  return page !== 'home' && page !== 'catalog' && page !== 'not-found'
}
