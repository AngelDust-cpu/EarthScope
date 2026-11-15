/**
 * plotly.js-basic-dist-min ships a minified UMD bundle without its own type
 * declarations. It exposes the same Plotly surface as `plotly.js`, so the
 * installed @types/plotly.js declarations are reused here.
 */
declare module 'plotly.js-basic-dist-min' {
  import * as Plotly from 'plotly.js'

  export = Plotly
}