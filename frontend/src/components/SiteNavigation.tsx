import { createContext, useContext, type ComponentPropsWithoutRef } from 'react'
import { ArrowLeft, ExternalLink, Globe2 } from 'lucide-react'
import { pageUrl, type SitePage } from '../lib/researchRoutes'

export const SiteNavigator = createContext<((page: SitePage) => void) | null>(null)

export function SiteLink({ to, ...props }: Omit<ComponentPropsWithoutRef<'a'>, 'href'> & { to: SitePage }) {
  const navigate = useContext(SiteNavigator)
  return <a {...props} href={pageUrl(to)} onClick={event => {
    props.onClick?.(event)
    if (event.defaultPrevented || !navigate || event.button !== 0 || event.metaKey || event.ctrlKey ||
      event.shiftKey || event.altKey || props.target || props.download) return
    event.preventDefault()
    navigate(to)
  }} />
}

export function SiteHeader({ showCatalog = false }: { showCatalog?: boolean }) {
  return <header className="station-header">
    <SiteLink to="home" className="station-brand" aria-label="EarthScope · Главная">
      <Globe2 size={28} strokeWidth={1.25} aria-hidden="true" />
      <span>EarthScope<span className="station-brand-dot">.</span></span>
    </SiteLink>
    <div className="station-header-links">
      {showCatalog && <SiteLink to="catalog" className="station-catalog-link"><ArrowLeft size={15} aria-hidden="true" />Все исследования</SiteLink>}
      <a className="station-source-link" href="https://power.larc.nasa.gov/" target="_blank" rel="noreferrer">NASA POWER <ExternalLink size={13} aria-hidden="true" /></a>
    </div>
  </header>
}
