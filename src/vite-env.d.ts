/// <reference types="vite/client" />

import type * as React from 'react';

/**
 * <model-viewer> is a web component loaded on demand by src/lib/lazyScript.ts,
 * so TypeScript has no intrinsic element for it. Declaring it here types the
 * props we actually pass (including the hotspot children used for part labels).
 */
declare global {
  namespace JSX {
    interface IntrinsicElements {
      'model-viewer': React.DetailedHTMLProps<
        React.HTMLAttributes<HTMLElement> & {
          ref?: React.Ref<any>;
          src?: string;
          alt?: string;
          poster?: string;
          'auto-rotate'?: boolean;
          'camera-controls'?: boolean;
          'interaction-prompt'?: string;
          'shadow-intensity'?: string;
          exposure?: string;
          bounds?: string;
          'environment-image'?: string;
          ar?: boolean;
          'ar-modes'?: string;
          'camera-orbit'?: string;
          'camera-target'?: string;
          'field-of-view'?: string;
          'min-camera-orbit'?: string;
          'max-camera-orbit'?: string;
          'auto-rotate-delay'?: number;
          'rotation-per-second'?: string;
          'shadow-softness'?: string;
          'tone-mapping'?: string;
          'touch-action'?: string;
          'disable-tap'?: boolean;
          'interaction-prompt-threshold'?: number;
          'data-visibility-attribute'?: string;
          loading?: string;
          reveal?: string;
        },
        HTMLElement
      >;
    }
  }
}

export {};
