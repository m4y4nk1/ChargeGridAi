import '@testing-library/jest-dom/vitest'

// jsdom doesn't implement scrollTo; TanStack Router's scroll restoration calls it on navigation.
window.scrollTo = () => {}
