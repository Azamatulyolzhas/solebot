// Navbar scroll effect
const navbar = document.getElementById("navbar");
window.addEventListener("scroll", () => {
  navbar.classList.toggle("scrolled", window.scrollY > 20);
}, { passive: true });

// Mobile burger menu
const burger = document.getElementById("burger");
const mobileMenu = document.getElementById("mobile-menu");
burger.addEventListener("click", () => {
  mobileMenu.classList.toggle("open");
});
mobileMenu.querySelectorAll("a").forEach(a => {
  a.addEventListener("click", () => mobileMenu.classList.remove("open"));
});

// Smooth scroll for anchor links
document.querySelectorAll('a[href^="#"]').forEach(a => {
  a.addEventListener("click", e => {
    const id = a.getAttribute("href").slice(1);
    const el = document.getElementById(id);
    if (el) {
      e.preventDefault();
      el.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  });
});

// Animated counter for trust stats
// Scroll-reveal animation
const revealObserver = new IntersectionObserver(entries => {
  entries.forEach(entry => {
    if (entry.isIntersecting) {
      entry.target.classList.add("revealed");
      revealObserver.unobserve(entry.target);
    }
  });
}, { threshold: 0.1 });

document.querySelectorAll(".feature-card, .step-card, .int-card, .plan-card").forEach(el => {
  el.classList.add("reveal");
  revealObserver.observe(el);
});

// Add reveal CSS inline
const style = document.createElement("style");
style.textContent = `
  .reveal { opacity: 0; transform: translateY(24px); transition: opacity .5s ease, transform .5s ease; }
  .revealed { opacity: 1; transform: translateY(0); }
`;
document.head.appendChild(style);
