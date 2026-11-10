export default defineAppConfig({
  ui: {
    colors: {
      primary: 'woad',
      secondary: 'madder',
      neutral: 'slate'
    },
    card: {
      slots: {
        root: 'rounded-lg',
        header: 'px-5 py-4 sm:px-5',
        body: 'p-5 sm:p-5'
      }
    }
  }
})
