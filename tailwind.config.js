/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      fontFamily: {
        optimus: ["OptimusPrinceps", "serif"],
        spline: ["SplineSans-Light", "sans-serif"],
      },
    },
  },
  plugins: [],
};
