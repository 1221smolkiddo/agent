document.addEventListener('DOMContentLoaded', () => {
    // Show/Hide password toggle logic
    const toggles = document.querySelectorAll('.password-toggle');
    toggles.forEach(toggle => {
        toggle.addEventListener('click', (e) => {
            e.preventDefault();
            const inputId = toggle.getAttribute('data-target');
            const input = document.getElementById(inputId);
            
            if (input.type === 'password') {
                input.type = 'text';
                toggle.textContent = 'Hide';
            } else {
                input.type = 'password';
                toggle.textContent = 'Show';
            }
        });
    });

    // Form submission loading state
    const form = document.querySelector('form');
    const submitBtn = document.getElementById('submit-btn');
    
    if (form) {
        form.addEventListener('submit', () => {
            submitBtn.classList.add('loading');
            submitBtn.disabled = true;
        });
    }
    
    // Auto-focus first input
    const firstInput = document.querySelector('input[type="text"], input[type="password"]');
    if (firstInput) {
        firstInput.focus();
    }

    // Generate background particles (matches success.html static particles)
    const container = document.getElementById('particles');
    if (container) {
        for (let i = 0; i < 5; i++) {
            const particle = document.createElement('div');
            particle.className = 'particle';
            container.appendChild(particle);
        }
    }
});
