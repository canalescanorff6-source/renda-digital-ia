document.addEventListener('DOMContentLoaded', () => {
  const flash = document.querySelectorAll('.flash');
  flash.forEach((el) => setTimeout(() => el.style.opacity = '0.15', 5500));

  if ('serviceWorker' in navigator && window.location.protocol.startsWith('http')) {
    navigator.serviceWorker.register('/service-worker.js').catch(() => {});
  }
});
