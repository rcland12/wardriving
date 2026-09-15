// Every page loads its data from the local API in the browser; there is nothing to prerender or
// render on the server, and MapLibre needs a real DOM.
export const ssr = false;
export const prerender = false;
