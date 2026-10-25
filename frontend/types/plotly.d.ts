// plotly.js-dist-min ships no type declarations and has no @types package
// (unlike plotly.js itself, which @types/react-plotly.js expects as its
// peer). PlotlyChart.tsx dynamically imports it purely to hand the raw
// module to react-plotly.js/factory's createPlotlyComponent(), so an
// untyped module declaration is sufficient -- nothing here calls into its
// API directly with a type-checked signature.
declare module "plotly.js-dist-min";
