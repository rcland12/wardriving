import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

export default defineConfig({
	plugins: [sveltekit()],
	server: {
		// Local only: the analyzer can publish to WiGLE, so never listen on the LAN by default.
		host: '127.0.0.1',
		port: 5174
	},
	preview: {
		host: '127.0.0.1',
		port: 5174
	}
});
