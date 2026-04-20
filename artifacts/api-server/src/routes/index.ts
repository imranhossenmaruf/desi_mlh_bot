import { Router, type IRouter } from "express";
import healthRouter from "./health";
import githubRouter from "./github";
import botRouter from "./bot";

const router: IRouter = Router();

router.use(healthRouter);
router.use(githubRouter);
router.use(botRouter);

export default router;
