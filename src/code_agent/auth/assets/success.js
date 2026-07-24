document.addEventListener('DOMContentLoaded', () => {
  let secondsLeft = 5;
  const countdownEl = document.getElementById('countdown');
  const textEl = document.getElementById('countdown-text');

  const interval = setInterval(() => {
    secondsLeft--;
    if (secondsLeft > 0) {
      countdownEl.textContent = `Closing in ${secondsLeft}...`;
    } else {
      clearInterval(interval);
      countdownEl.textContent = "Closing...";
      
      // Attempt to close the window
      try {
        window.close();
      } catch (e) {
        // Ignore error
      }
      
      // Fallback if browser blocks window.close()
      setTimeout(() => {
        textEl.textContent = "You may safely close this window.";
        countdownEl.style.display = 'none';
      }, 500);
    }
  }, 1000);
});
