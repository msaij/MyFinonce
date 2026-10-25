// plotly.js-cartesian-dist-min ships no type declarations of its own (unlike the full
// plotly.js-dist-min bundle). This app already treats Plotly figure data loosely
// (`as never` casts in PlotlyChart.tsx and every page building a figure) rather than
// fighting for full type safety against Plotly's API surface, so an untyped ambient
// module declaration here matches that existing practice -- not a regression in rigor.
declare module "plotly.js-cartesian-dist-min";
