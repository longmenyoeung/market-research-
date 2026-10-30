import {defineConfig} from '@playwright/test';
export default defineConfig({testDir:'./tests',timeout:60000,use:{baseURL:'http://127.0.0.1:5173',headless:true,viewport:{width:1440,height:1000},screenshot:'only-on-failure'},webServer:{command:'npm run dev',url:'http://127.0.0.1:5173',reuseExistingServer:!process.env.CI,timeout:30000},workers:1});
