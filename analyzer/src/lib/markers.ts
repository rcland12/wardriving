import { SECURITY, SECURITY_ORDER } from './security';
import type { Security } from './types';

/**
 * Map marker bitmaps, one per security class, drawn at 2x for sharp edges. Shapes and colors
 * mirror SecurityMark.svelte and the Pi's maprender.draw_marker: a colored fill with a dark
 * outline, so every class reads on light streets, dark streets and satellite imagery alike.
 */

const SIZE = 40; // bitmap pixels; registered at pixelRatio 2, so 20 CSS px
const OUTLINE = '#0b0f14';

function draw(sec: Security): ImageData {
	const canvas = document.createElement('canvas');
	canvas.width = canvas.height = SIZE;
	const ctx = canvas.getContext('2d')!;
	const { color, shape } = SECURITY[sec];
	const c = SIZE / 2;
	ctx.lineJoin = 'round';
	ctx.lineWidth = 4;
	ctx.strokeStyle = OUTLINE;
	ctx.fillStyle = color;
	ctx.beginPath();
	switch (shape) {
		case 'circle':
			ctx.arc(c, c, 13, 0, Math.PI * 2);
			break;
		case 'square':
			ctx.roundRect(c - 12, c - 12, 24, 24, 3);
			break;
		case 'diamond':
			ctx.moveTo(c, c - 16);
			ctx.lineTo(c + 16, c);
			ctx.lineTo(c, c + 16);
			ctx.lineTo(c - 16, c);
			ctx.closePath();
			break;
		case 'triangle':
			ctx.moveTo(c, c - 15);
			ctx.lineTo(c + 16, c + 13);
			ctx.lineTo(c - 16, c + 13);
			ctx.closePath();
			break;
		case 'plus': {
			const a = 5;
			const l = 15;
			ctx.moveTo(c - a, c - l);
			ctx.lineTo(c + a, c - l);
			ctx.lineTo(c + a, c - a);
			ctx.lineTo(c + l, c - a);
			ctx.lineTo(c + l, c + a);
			ctx.lineTo(c + a, c + a);
			ctx.lineTo(c + a, c + l);
			ctx.lineTo(c - a, c + l);
			ctx.lineTo(c - a, c + a);
			ctx.lineTo(c - l, c + a);
			ctx.lineTo(c - l, c - a);
			ctx.lineTo(c - a, c - a);
			ctx.closePath();
			break;
		}
		case 'ring':
			ctx.arc(c, c, 12, 0, Math.PI * 2);
			ctx.lineWidth = 10;
			ctx.stroke();
			ctx.lineWidth = 6;
			ctx.strokeStyle = color;
			ctx.stroke();
			return ctx.getImageData(0, 0, SIZE, SIZE);
	}
	ctx.fill();
	ctx.stroke();
	return ctx.getImageData(0, 0, SIZE, SIZE);
}

export function markerImages(): { name: string; image: ImageData }[] {
	return SECURITY_ORDER.map((sec) => ({ name: `sec-${sec}`, image: draw(sec) }));
}
