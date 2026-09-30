/* Shared native player setup for feed, sets, and label review. */
window.HanniMedia = {
  create(kind, label) {
    const media = document.createElement(kind === 'video' ? 'video' : 'img');
    media.referrerPolicy = 'no-referrer';
    if (kind === 'video') {
      media.autoplay = true;
      media.controls = false;
      media.defaultMuted = true;
      media.loop = true;
      media.muted = true;
      media.preload = 'auto';
      media.playsInline = true;
    } else {
      media.alt = label || 'Feed item';
      media.loading = 'eager';
      media.decoding = 'async';
    }
    return media;
  },
};
