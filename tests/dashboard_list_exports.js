/* Classic browser scripts expose these functions on window. A concatenated
   CommonJS test script needs to provide that same interface explicitly. */
Object.assign(window, {
    document, escHtml, dashboardActivityHtml, dashboardProgressHtml, dashboardAgentGlyphHtml,
    dashboardAgentGlyphKey, dashboardPaneLine, dashboardPaneHover, dashboardAgentName,
    dashboardAgentMarkState, dashboardCrewContext, dashboardCrewChipHtml,
    dashboardWorkspaceLabel, dashboardSessionColourStyle, dashboardTotalsText,
    openDashboardTarget
});
