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
function animateCounter(el, target, suffix = "") {
  let start = 0;
  const duration = 1200;
  const step = timestamp => {
    if (!start) start = timestamp;
    const progress = Math.min((timestamp - start) / duration, 1);
    const eased = 1 - Math.pow(1 - progress, 3);
    el.textContent = Math.round(eased * target) + suffix;
    if (progress < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

// Trigger counters when hero enters viewport
const heroObserver = new IntersectionObserver(entries => {
  if (entries[0].isIntersecting) {
    animateCounter(document.getElementById("stat-shops"), 50, "+");
    animateCounter(document.getElementById("stat-msgs"), 10000, "+");
    heroObserver.disconnect();
  }
}, { threshold: 0.3 });
const heroSection = document.querySelector(".hero");
if (heroSection) heroObserver.observe(heroSection);

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
