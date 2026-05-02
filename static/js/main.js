(function () {
    function setupNavToggle() {
        const toggle = document.querySelector('[data-nav-toggle]');
        const menu = document.querySelector('[data-nav-menu]');
        if (!toggle || !menu) return;

        toggle.addEventListener('click', function () {
            menu.classList.toggle('is-open');
        });

        document.addEventListener('click', function (event) {
            if (!menu.classList.contains('is-open')) return;
            if (menu.contains(event.target) || toggle.contains(event.target)) return;
            menu.classList.remove('is-open');
        });
    }

    function setupFlashMessages() {
        const flashes = document.querySelectorAll('[data-flash]');
        flashes.forEach(function (flash) {
            const closeButton = flash.querySelector('[data-flash-close]');
            if (closeButton) {
                closeButton.addEventListener('click', function () {
                    flash.remove();
                });
            }

            setTimeout(function () {
                if (flash && flash.parentNode) {
                    flash.style.opacity = '0';
                    setTimeout(function () {
                        if (flash.parentNode) flash.remove();
                    }, 250);
                }
            }, 5000);
        });
    }

    function setupRevealAnimations() {
        const reveals = document.querySelectorAll('.reveal');
        if (!reveals.length) return;

        const observer = new IntersectionObserver(
            function (entries) {
                entries.forEach(function (entry) {
                    if (entry.isIntersecting) {
                        entry.target.classList.add('reveal-visible');
                        observer.unobserve(entry.target);
                    }
                });
            },
            { threshold: 0.12 }
        );

        reveals.forEach(function (node, index) {
            node.style.transitionDelay = (index * 40) + 'ms';
            observer.observe(node);
        });
    }

    function setupFileInputFeedback() {
        const input = document.querySelector('[data-file-input]');
        if (!input) return;

        const helper = document.createElement('p');
        helper.className = 'helper-text';
        helper.style.marginTop = '0.35rem';
        input.parentNode.appendChild(helper);

        input.addEventListener('change', function () {
            if (input.files && input.files.length > 0) {
                helper.textContent = 'Selected file: ' + input.files[0].name;
            } else {
                helper.textContent = '';
            }
        });
    }

    document.addEventListener('DOMContentLoaded', function () {
        setupNavToggle();
        setupFlashMessages();
        setupRevealAnimations();
        setupFileInputFeedback();
    });
})();
