const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.launch();
  const context = await browser.newContext();
  const page = await context.newPage();

  try {
    console.log('Navigating to local frontend...');
    await page.goto('http://localhost:5173');
    await page.waitForLoadState('networkidle');

    console.log('Creating a new task to test automatic team selection...');
    await page.click('text="New task"');
    await page.fill('input[name="title"]', 'Build a new REST API endpoint');
    await page.fill('textarea[name="request"]', 'We need a new endpoint for fetching market data.');
    
    await page.click('button[type="submit"]:has-text("Submit task")');
    await page.waitForLoadState('networkidle');

    console.log('Waiting for team selection...');
    // The details pane should appear
    await page.waitForSelector('text=Build a new REST API endpoint', { timeout: 10000 });
    
    // Check if team selection logic ran
    await page.waitForSelector('text=Team Rationale', { timeout: 10000 });
    await page.waitForSelector('text=Required Capabilities', { timeout: 10000 });
    
    console.log('Team selection found in UI!');
    
    // Check if selection is completed
    await page.waitForSelector('text=completed', { timeout: 10000 });
    console.log('Team selection completed successfully.');

    await browser.close();
    console.log('SUCCESS: Team selection integration works.');
    process.exit(0);

  } catch (err) {
    console.error('FAILED:', err);
    await browser.close();
    process.exit(1);
  }
})();
