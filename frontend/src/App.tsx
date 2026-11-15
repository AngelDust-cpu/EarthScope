import { lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react'
import { ArrowDown, ArrowRight, ChartNoAxesCombined, Database, MapPin, Search } from 'lucide-react'
import { SiteHeader, SiteLink, SiteNavigator } from './components/SiteNavigation'
import { isResearchMode, pageFromPath, pageUrl, researchModes, type ResearchMode, type SitePage } from './lib/researchRoutes'
import './site.css'

const ResearchWorkspace = lazy(() => import('./ResearchWorkspace'))

const examples: Record<ResearchMode, string> = {
  single: 'Как изменилась температура в Бишкеке с 1990 года?',
  compare: 'Где потепление шло быстрее — в Бишкеке или Рейкьявике?',
  profile: 'Как за последние десятилетия менялись осадки, температура и экстремумы в моём регионе?',
  anomalies: 'Какие годы были теплее среднего за 1991–2020?',
  seasonal: 'Как менялось количество осадков в феврале?',
  extremes: 'Стало ли больше дней с температурой выше +30 °C?',
  'regional-map': 'Где на карте мира видны наиболее выраженные тренды?',
  detective: 'Как провести первое исследование от вопроса до научного вывода?',
}

const issykPhoto = 'https://science.nasa.gov/earth/earth-observatory/lake-issyk-kul-85918/'
const tienShanPhoto = 'https://science.nasa.gov/earth/earth-observatory/snow-and-sand-in-central-asia-144757/'

function PublicPage({ page }: { page: 'home' | 'catalog' | 'not-found' }) {
  const heading = useRef<HTMLHeadingElement>(null)
  useEffect(() => { heading.current?.focus({ preventScroll: true }) }, [page])

  return <div className="earthscope-site">
    <SiteHeader />
    {page === 'home' ? (
      <main>
        <section className="site-intro" aria-labelledby="site-home-title">
          <div className="site-container site-intro-copy site-enter">
            <h1 id="site-home-title" ref={heading} tabIndex={-1}>Как меняется климат Земли?</h1>
            <p>Выберите место на карте и исследуйте, как за десятилетия изменились температура, осадки и частота экстремальных событий.</p>
            <SiteLink to="catalog" className="site-primary">Начать исследование <ArrowRight size={18} aria-hidden="true" /></SiteLink>
          </div>
          <figure className="site-hero-photo">
            <img src={`${import.meta.env.BASE_URL}issyk-kul-nasa.webp`} alt="Озеро Иссык-Куль среди заснеженных гор Тянь-Шаня" width={1440} height={957} fetchPriority="high" />
            <figcaption>
              <span>Иссык-Куль, Кыргызстан</span>
              <a href={issykPhoto} target="_blank" rel="noreferrer">Снимок с МКС · NASA, 2015</a>
            </figcaption>
          </figure>
          <a href="#site-start-title" className="site-scroll-link">
            <span>Вопросы для быстрого старта</span><ArrowDown size={19} aria-hidden="true" />
          </a>
        </section>
        <div className="site-container">
        <section className="site-start" aria-labelledby="site-start-title">
          <h2 id="site-start-title" tabIndex={-1}>С какого вопроса начать?</h2>
          <div className="site-start-grid">
            <SiteLink to="single" className="site-start-link"><MapPin size={19} aria-hidden="true" />{examples.single}<ArrowRight size={17} aria-hidden="true" /></SiteLink>
            <SiteLink to="regional-map" className="site-start-link"><ChartNoAxesCombined size={19} aria-hidden="true" />{examples['regional-map']}<ArrowRight size={17} aria-hidden="true" /></SiteLink>
            <SiteLink to="detective" className="site-start-link"><Search size={19} aria-hidden="true" />{examples.detective}<ArrowRight size={17} aria-hidden="true" /></SiteLink>
          </div>
        </section>
        <section className="site-method" aria-labelledby="site-data-title">
          <div>
            <Database size={23} aria-hidden="true" />
            <h2 id="site-data-title">От данных NASA к ответу</h2>
            <p>EarthScope использует открытые данные NASA POWER на основе реанализа MERRA-2. Значения относятся к ячейкам географической сетки. Результат в выбранной точке может отличаться от измерений ближайшей метеостанции.</p>
            <a href="https://power.larc.nasa.gov/" target="_blank" rel="noreferrer">Подробнее о NASA POWER <ArrowRight size={15} aria-hidden="true" /></a>
          </div>
          <ol>
            <li><strong>Задайте место и период.</strong> Выберите точку на карте или укажите координаты.</li>
            <li><strong>Оцените изменения.</strong> Сопоставьте значения по годам, тренд и его неопределённость.</li>
            <li><strong>Сохраните результат.</strong> В режимах с экспортом скачайте таблицу CSV или научный PDF-отчёт.</li>
          </ol>
        </section>
        </div>
      </main>
    ) : page === 'catalog' ? (
      <main className="site-container site-catalog">
        <header className="site-catalog-intro site-enter">
          <div>
            <h1 ref={heading} tabIndex={-1}>Выберите исследование</h1>
            <p>Изучите климат одного места, сравните регионы или откройте карту мира. Для первого знакомства «Детектив» проведёт вас от вопроса до результата.</p>
          </div>
          <figure className="site-photo site-catalog-photo">
            <img src={`${import.meta.env.BASE_URL}tien-shan-nasa.webp`} alt="Снег на восточном Тянь-Шане рядом с пустыней Такла-Макан" width={648} height={900} />
            <figcaption><a href={tienShanPhoto} target="_blank" rel="noreferrer">Тянь-Шань · снимок с МКС, NASA, 2018</a></figcaption>
          </figure>
        </header>
        <ul className="site-research-grid">
          {researchModes.map(({ value, label, hint, Icon }) => (
            <li key={value}>
              <SiteLink to={value} className="site-research-card" aria-labelledby={`catalog-${value}`}>
                <Icon size={22} aria-hidden="true" />
                <h2 id={`catalog-${value}`}>{label}</h2>
                <p>{hint}</p>
                <p className="site-card-example">{examples[value]}</p>
                <ArrowRight className="site-card-arrow" size={18} aria-hidden="true" />
              </SiteLink>
            </li>
          ))}
        </ul>
        <p className="site-catalog-note">Доступный период зависит от режима. У каждого результата указаны источник данных и ограничения анализа.</p>
      </main>
    ) : (
      <main className="site-container site-catalog">
        <h1 ref={heading} tabIndex={-1}>Страница не найдена</h1>
        <SiteLink to="catalog" className="site-primary">Все исследования <ArrowRight size={18} aria-hidden="true" /></SiteLink>
      </main>
    )}
  </div>
}

export default function App() {
  const [page, setPage] = useState(() => pageFromPath(window.location.pathname))
  const [workspaceOpened, setWorkspaceOpened] = useState(() => isResearchMode(page))
  const changePage = useCallback((next: SitePage | 'not-found') => {
    setPage(next)
    if (isResearchMode(next)) setWorkspaceOpened(true)
  }, [])

  useEffect(() => {
    function restorePage() { changePage(pageFromPath(window.location.pathname)) }
    window.addEventListener('popstate', restorePage)
    return () => window.removeEventListener('popstate', restorePage)
  }, [changePage])

  const navigate = useCallback((next: SitePage) => {
    if (pageFromPath(window.location.pathname) === next) return
    window.history.pushState(null, '', pageUrl(next))
    changePage(next)
  }, [changePage])

  useEffect(() => {
    window.scrollTo(0, 0)
    const title = page === 'catalog' ? 'Исследования' : page === 'not-found' ? 'Страница не найдена' :
      researchModes.find(mode => mode.value === page)?.title
    document.title = title ? `${title} | EarthScope` : 'EarthScope — исследование климата Земли'
  }, [page])

  return <SiteNavigator.Provider value={navigate}>
    {!isResearchMode(page) && <PublicPage page={page} />}
    {workspaceOpened && <Suspense fallback={isResearchMode(page) ? <p role="status" className="site-container p-6">Открываем исследование…</p> : null}>
      <ResearchWorkspace view={isResearchMode(page) ? page : null} />
    </Suspense>}
  </SiteNavigator.Provider>
}
