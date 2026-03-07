/**
 * ime_zh.js — JS 内置拼音输入法，完全绕过 OS/GTK IM 栈
 * 适用于 WebKit2GTK + WSLg 等系统 IME 无法工作的场景
 *
 * 用法：Ctrl+Space 切换中/英，字母键输入拼音，数字键 1-9 或 Space 选字
 */
(function () {
  'use strict';

  // ── 拼音字典 ─────────────────────────────────────────────────────────────
  // 格式: { 拼音: "按频率排列的汉字串" }
  const D = {
    a:"啊阿呵",ai:"爱哀矮碍艾挨唉",an:"安暗按案岸氨庵俺",ang:"昂",ao:"奥傲坳懊熬",
    ba:"吧把爸八拔霸罢坝疤芭靶扒",bai:"白百败摆拜伯柏",ban:"半班般板版办绑搬颁斑拌",
    bang:"帮棒磅傍膀蚌榜谤",bao:"包报宝爆抱薄保胞堡豹暴饱剥鲍",
    bei:"被背北倍贝悲辈备杯碑惫卑堡呸",ben:"本笨奔苯",beng:"崩泵绷",
    bi:"比必笔闭壁避币毕鼻碧彼逼庇弊陛辟痹鄙",bian:"变边便遍鞭辩辨扁贬编",
    biao:"标表彪膘镖",bie:"别憋瘪鳖",bin:"宾滨槟鬓彬",bing:"病冰饼丙并兵柄秉禀",
    bo:"波伯博播脖薄驳泊颇玻剥菠钵搏铂帛",bu:"不部步补布捕堡哺簿埔卜怖",
    ca:"擦",cai:"才菜财彩裁踩猜采睬",can:"参残惭惨蚕灿掺",cang:"藏仓苍舱沧",
    cao:"草曹槽操嘈糙",ce:"策测厕侧册",ceng:"层曾蹭",
    cha:"查茶差插察叉诧岔搽",chai:"拆柴",chan:"产禅缠颤掺",
    chang:"常长场厂唱畅昌尝偿倡猖",chao:"超朝抄巢炒嘲钞",che:"车扯撤彻",
    chen:"陈晨沉尘臣趁衬称",cheng:"成城程承称乘盛诚撑惩澄秤逞",
    chi:"吃持池迟尺耻斥翅赤驰齿",chong:"重冲充虫崇",
    chou:"抽愁绸丑臭仇酬筹踌",
    chu:"出处楚础初储触除厨锄雏畜",chuai:"揣",chuan:"传船穿串川喘",
    chuang:"床创窗撞",chui:"吹锤垂",chun:"春纯淳唇蠢",chuo:"戳绰",
    ci:"此词次磁辞刺雌慈",cong:"从聪丛",cou:"凑",cu:"粗促醋卒",
    cuan:"蹿篡窜",cui:"催脆翠崔粹瘁",cun:"村存寸",cuo:"错措挫撮磋",
    da:"大打达答搭瘩",dai:"带代待贷戴袋呆怠逮",dan:"单担但淡弹蛋胆诞",
    dang:"当党档挡荡",dao:"到道导倒刀岛盗稻叨捣",de:"的地得",
    deng:"等灯登凳邓瞪",di:"地第底的弟低敌抵滴迪帝递",
    dian:"点电典店颠垫淀殿碘巅",diao:"掉调刁吊钓",die:"跌蝶迭碟叠",
    ding:"定顶订钉丁盯叮",diu:"丢",dong:"东动洞冻懂栋董冬",
    dou:"都斗豆逗抖陡兜",du:"度读独都堵肚渡杜督毒赌妒镀",
    duan:"断段短端锻",dui:"对堆队",dun:"顿敦盾墩蹲钝囤",duo:"多朵夺躲堕舵惰",
    e:"恶额俄鹅饿愕扼厄鄂",en:"恩嗯",er:"二而耳尔儿",
    fa:"发法罚乏伐阀筏",fan:"反饭范烦繁翻泛凡犯贩番",fang:"方房放防访纺仿妨",
    fei:"飞非费肺废沸匪",fen:"分份粉纷奋坟愤",feng:"风封丰锋蜂峰凤奉缝逢",
    fo:"佛",fu:"父夫服福负付辅府腐富副扶幅复赴浮",
    ga:"嘎",gai:"该改盖概",gan:"感干敢赶肝杆",gang:"钢港刚纲岗",
    gao:"高告搞稿",ge:"个格歌各革葛隔阁割搁鸽",gei:"给",gen:"根跟",
    geng:"更耕梗",gou:"够购钩沟构勾狗",gu:"古股故顾估骨鼓固谷雇蛊",
    gua:"挂刮瓜",guai:"怪乖拐",guan:"关官管观惯冠贯馆",guang:"广光逛",
    gui:"贵规归鬼柜轨桂诡",gun:"棍滚",guo:"国过果锅郭裹",
    ha:"哈",hai:"海还害骸骇孩",han:"汉寒含喊旱函汗憾悍焊",hang:"航行杭",
    hao:"好号浩颢毫耗",he:"和合河何喝核贺赫盒荷",hei:"黑嘿",hen:"很恨狠",
    heng:"衡横哼亨",hong:"红洪哄宏鸿弘",hou:"后候厚喉猴吼",
    hu:"护互虎湖胡户乎呼弧壶忽糊狐",hua:"化花画话华",huai:"怀坏",
    huan:"换还欢环幻缓患唤焕",huang:"黄皇晃荒谎慌惶",
    hui:"会回惠汇灰毁挥辉悔恢秽晦彗婚",hun:"婚浑魂混",huo:"活火或获货伙惑",
    ji:"机几己计记技济疾级既击集基即季继给极寂积籍激迹及吉辑缉",
    jia:"家加价假甲架嫁驾佳颊",
    jian:"见间建减简监键检健坚件兼尖",jiang:"江讲降将奖疆僵",
    jiao:"交叫脚教角觉较骄娇搅",jie:"解结街节接界阶借截姐届戒捷",
    jin:"进近今金禁尽锦仅紧浸晋",jing:"经京精景静竟境警颈茎晶",
    jiong:"炯迥窘",jiu:"就旧九究久救酒纠",
    ju:"局举据距拒聚具巨剧居句咀菊",juan:"卷捐倦娟",
    jue:"觉决绝缺掘诀爵",jun:"军均君菌竣峻",
    ka:"卡咖",kai:"开楷",kan:"看刊砍堪勘",kang:"抗扛康慷炕",kao:"靠考烤拷",
    ke:"可刻课科客克壳渴颗",ken:"肯垦",keng:"坑",kong:"空控恐孔",
    kou:"口扣寇叩",ku:"苦库哭裤枯",kua:"垮块夸",kuai:"快块筷",kuan:"宽款",
    kuang:"矿况扩狂框",kui:"愧亏溃馈",kun:"困昆捆",kuo:"扩括阔",
    la:"拉啦辣腊",lai:"来赖",lan:"蓝烂懒栏览滥拦",lang:"郎浪廊",lao:"老牢劳捞",
    le:"了乐勒",lei:"雷累泪类肋",leng:"冷愣棱",
    li:"利力历里理礼立离例粒吏丽厉黎鲤狸",lian:"联脸练恋连怜廉链敛",
    liang:"量两亮凉良梁",liao:"了料聊疗辽瞭",lie:"列烈劣裂猎",
    lin:"林临淋邻灵",ling:"零令灵领另龄陵",liu:"六流留柳刘溜遛",
    long:"龙弄隆笼拢",lou:"楼漏陋露搂",
    lu:"路陆录绿虑旅炉卢鲁露鹿掠",luan:"乱卵",lue:"略掠",lun:"论轮仑沦",
    luo:"落罗络螺锣",
    ma:"马妈吗嘛骂麻",mai:"买卖迈麦埋",man:"满慢漫蛮",mang:"忙盲茫",
    mao:"毛矛冒帽猫貌",mei:"每没美妹眉媒",men:"们门闷",meng:"梦蒙猛盟",
    mi:"米密秘迷谜觅",mian:"面免棉绵",miao:"妙苗庙秒描瞄",mie:"灭蔑",
    min:"民敏闽",ming:"命名明鸣铭冥",mo:"末么模摸磨默抹莫膜",mou:"某谋",
    mu:"木目墓幕牧暮亩",
    na:"那拿哪纳呐",nai:"奶耐乃",nan:"南难男",nao:"脑恼闹",ne:"呢",
    nei:"内那",nen:"嫩",neng:"能",ni:"你泥拟逆腻",nian:"年念粘",
    niang:"娘酿",niao:"鸟尿",nie:"捏镊",nin:"您",ning:"宁凝柠",niu:"牛扭钮",
    nong:"农浓弄侬",nu:"努怒奴",nuan:"暖",nuo:"诺挪懦",
    o:"哦噢",ou:"欧偶",
    pa:"怕爬趴啪",pai:"排派拍牌",pan:"判盼攀盘叛",pang:"旁庞胖",pao:"跑泡炮抱袍",
    pei:"配陪赔佩培",pen:"盆喷",peng:"朋碰棚",
    pi:"皮批匹啤疲脾披劈辟僻",pian:"片篇偏骗",piao:"飘票漂",pie:"撇瞥",
    pin:"品拼贫",ping:"平评屏瓶凭乒",po:"破坡婆迫泼",pu:"普铺朴浦扑葡脯",
    qi:"起其期气企器骑七奇戚棋旗祈齐弃妻岐蹊",qia:"掐恰",
    qian:"前钱千签迁牵潜欠歉浅铅谦",qiang:"强墙抢枪腔",
    qiao:"桥巧敲悄乔侨撬翘",qie:"切茄且窃",qin:"亲琴勤侵秦",
    qing:"请情清青轻庆晴倾",qiong:"穷穹琼",qiu:"求球秋丘囚",qu:"去取区趋屈",
    quan:"全权劝拳泉圈",que:"却确缺",qun:"群裙",
    ran:"然染燃",rang:"让壤嚷",rao:"绕惹",re:"热惹",
    ren:"人任认仁忍刃韧",reng:"仍扔",ri:"日",rong:"容荣融溶绒",
    rou:"肉柔揉",ru:"入如孺乳汝",ruan:"软",run:"润",ruo:"若弱",
    sa:"撒洒萨",sai:"塞赛腮",san:"三散伞叁",sang:"桑嗓丧",sao:"扫骚嫂",
    se:"色涩",sen:"森",sha:"沙杀傻啥纱",shai:"晒筛",shan:"山扇闪",
    shang:"上商赏尚伤",shao:"少稍捎梢绍",she:"社设射摄舍蛇折",
    shen:"身神深什沈审慎伸参",sheng:"生声省圣升胜剩",
    shi:"是时事实世使式示石市适识失师势施视始十试史士室尸拭矢侍",
    shou:"收手受守首瘦寿兽",shu:"书树数属输束述熟鼠术恕墅",
    shua:"刷耍",shuai:"帅摔衰",shuan:"拴栓",shuang:"双霜爽",shui:"水睡谁",
    shun:"顺瞬",shuo:"说所缩",si:"四私思死斯丝司",song:"送松宋颂",sou:"搜",
    su:"素速虽俗塑溯粟肃",suan:"算酸",sui:"虽随碎岁",sun:"损孙",suo:"所缩锁索",
    ta:"他她它塌踏",tai:"太台泰态",tan:"谈弹探炭叹贪摊",
    tang:"糖堂躺趟汤唐棠",tao:"跑讨套逃",te:"特",teng:"腾疼",
    ti:"题提体替梯蹄",tian:"田天甜填添",tiao:"跳挑条调",tie:"铁贴",
    ting:"听停厅庭挺亭",tong:"同统通痛童",tou:"头投透偷",
    tu:"图土突徒途吐涂",tuan:"团",tui:"推腿退",tun:"吞屯",tuo:"脱托拓",
    wa:"挖哇蛙娃",wai:"外歪",wan:"完玩万弯晚碗湾蔓挽",wang:"王网往望忘旺汪",
    wei:"为位维围未委微卫威胃味违",wen:"问文稳温闻吻纹",weng:"翁嗡瓮",
    wo:"我握卧窝蜗涡",wu:"五无务雾舞误物武屋",
    xi:"系西席戏习希细析稀吸息隙喜媳嬉",xia:"下夏虾吓峡侠",
    xian:"先现限显线险鲜仙掀献嫌羡闲",xiang:"想相向象香享像降乡",
    xiao:"小笑校消效晓",xie:"些写谢协血亵蟹携楔",xin:"心新信薪芯辛欣",
    xing:"行星形型性醒幸姓",xiong:"雄胸凶熊兄",xiu:"秀修朽锈羞",
    xu:"需虚续许序须徐旭蓄",xuan:"选旋悬宣玄炫绚",xue:"学雪血削",
    xun:"寻训讯询驯迅殉",
    ya:"啊呀压牙雅亚炸鸦",yan:"言研严眼烟验颜延换氧宴焰砚咽淹",
    yang:"养样羊阳洋扬仰仗",yao:"要姚药摇邀妖遥谣",ye:"也夜业叶野液耶爷",
    yi:"已以一义意移支医依疑益艺忆易宜议",yin:"因音引印银",
    ying:"应英影迎映营硬",yo:"哟",yong:"用永泳勇拥",
    you:"有由友优又游右幼尤悠诱酉疣",
    yu:"与于鱼语育遇预余誉愈浴域欲逾",yuan:"员远元原源愿院怨圆园缘",
    yue:"月越约跃阅悦岳",yun:"云运允晕孕韵",
    za:"杂咋砸",zai:"再在载",zan:"咱赞暂",zang:"脏藏葬",zao:"早造遭糟",
    ze:"则泽责",zei:"贼",zen:"怎",zeng:"增曾",
    zha:"扎炸闸渣榨",zhai:"摘宅寨债",zhang:"长章张撑掌丈涨障仗",
    zhao:"找着照招召兆",zhe:"这者折着遮",zhen:"真振针镇阵诊",
    zheng:"正证政整争挣征",
    zhi:"知只制至地直治智植纸志之支止职脂质置",zhong:"中种重众终忠钟",
    zhou:"周州洲宙轴皱粥",zhu:"主注住助著株珠祝竹",zhua:"抓",zhuai:"拽",
    zhuan:"转专砖",zhuang:"装壮庄撞",zhui:"追坠揣",zhun:"准",
    zhuo:"着桌灼捉浊",zi:"子自字资姿滋",zong:"总综宗棕纵",zou:"走奏",
    zu:"足族祖阻组",zuan:"钻",zui:"最罪嘴醉",zun:"尊遵",zuo:"作坐做左座"
  };

  // ── 常用词组 ─────────────────────────────────────────────────────────────
  // 格式: { "完整拼音": "汉字" }
  const W = {
    // 日常用语
    nihao:"你好",zaijian:"再见",xiexie:"谢谢",xiethanks:"谢谢",duibuqi:"对不起",
    meiguanxi:"没关系",bukeqi:"不客气",keyi:"可以",meiyou:"没有",zhidao:"知道",
    buzhidao:"不知道",qingwen:"请问",henhao:"很好",haode:"好的",
    duile:"对了",shide:"是的",bushi:"不是",keneng:"可能",yiding:"一定",
    // 国家地区
    zhongguo:"中国",meiguo:"美国",riben:"日本",yingguo:"英国",
    deguo:"德国",faguo:"法国",eluosi:"俄罗斯",hanguo:"韩国",
    // 城市
    beijing:"北京",shanghai:"上海",guangzhou:"广州",shenzhen:"深圳",
    chengdu:"成都",hangzhou:"杭州",nanjing:"南京",wuhan:"武汉",
    xian:"西安",tianjin:"天津",chongqing:"重庆",suzhou:"苏州",
    // 时间
    jintian:"今天",mingtian:"明天",zuotian:"昨天",houtian:"后天",
    qiantian:"前天",zhezhou:"这周",xaizhou:"下周",shangzhou:"上周",
    shangwu:"上午",xiawu:"下午",wanshang:"晚上",zaoshang:"早上",zhongwu:"中午",
    xingqiri:"星期日",xingqiyi:"星期一",xingqier:"星期二",xingqisan:"星期三",
    xingqisi:"星期四",xingqiwu:"星期五",xingqiliu:"星期六",
    zhoumou:"周末",jinnian:"今年",mingnian:"明年",qunian:"去年",
    zaochen:"早晨",shijian:"时间",riqing:"日期",
    // 人物关系
    gongzuo:"工作",xuexiao:"学校",xuesheng:"学生",laoshi:"老师",
    pengyou:"朋友",tongxue:"同学",xiansheng:"先生",nvshi:"女士",
    tongshi:"同事",lingdao:"领导",zhuanjia:"专家",yonghu:"用户",
    // 常用动词
    wenti:"问题",jiejue:"解决",bangzhu:"帮助",shenghuo:"生活",
    xuexi:"学习",kaishi:"开始",jieshu:"结束",wancheng:"完成",
    liaojie:"了解",zhunbei:"准备",jixu:"继续",tingzhi:"停止",
    xiugai:"修改",shanchu:"删除",tianjia:"添加",chakan:"查看",
    // 科技
    rengongzhineng:"人工智能",rengong:"人工",zhineng:"智能",
    jisuanji:"计算机",ruanjian:"软件",yingyong:"应用",xitong:"系统",
    biancheng:"编程",chengxu:"程序",daima:"代码",jishu:"技术",
    shujuku:"数据库",fuwuqi:"服务器",jiekou:"接口",moxing:"模型",
    wangluo:"网络",dianhua:"电话",shouji:"手机",diannao:"电脑",
    kaifa:"开发",chuangxin:"创新",jieguo:"结果",cuowu:"错误",
    suanfa:"算法",zidonghua:"自动化",yunjishan:"云计算",
    qianrushi:"嵌入式",kaiyanuan:"开源",renlianshibie:"人脸识别",
    ziranyuyan:"自然语言",shenduexuexi:"深度学习",jiqixuexi:"机器学习",
    // 工作
    fangan:"方案",gongneng:"功能",yaoqiu:"要求",biaoge:"表格",
    wenzhang:"文章",ziliao:"资料",xinxi:"信息",shuju:"数据",
    yuce:"预测",fenxi:"分析",baogao:"报告",zongjie:"总结",
    fuzeren:"负责人",xiangmu:"项目",tuandui:"团队",huiyi:"会议",
    jihua:"计划",mubiao:"目标",chengguo:"成果",xiaolv:"效率",
    // 情感
    kuaile:"快乐",gaoxing:"高兴",manyi:"满意",nanguo:"难过",
    tongyi:"同意",butongyi:"不同意",juede:"觉得",renwei:"认为",
    xihuan:"喜欢",taoli:"讨厌",xingqu:"兴趣",guanxi:"关系",
    // 逻辑连词
    suoyi:"所以",yinwei:"因为",danshi:"但是",haishi:"还是",
    yijing:"已经",zhengzai:"正在",jianglai:"将来",guoqu:"过去",
    women:"我们",nimen:"你们",tamen:"他们",dajia:"大家",
    zhege:"这个",nage:"那个",shenme:"什么",zenme:"怎么",
    weishenme:"为什么",zenmeyang:"怎么样",duoshao:"多少",jige:"几个",
    yiqi:"一起",yiqian:"以前",yihou:"以后",xianzai:"现在",
    zheyang:"这样",nayang:"那样",dangran:"当然",
    qishi:"其实",liru:"例如",biru:"比如",tongshii:"同时",
    suiran:"虽然",raner:"然而",yifangmian:"一方面",lingfangmian:"另一方面",
    yiban:"一般",putong:"普通",teshu:"特殊",zhongyao:"重要",
    jiben:"基本",quanbu:"全部",bufen:"部分",zonghe:"综合",
    // 常用短句
    ninhao:"您好",qingzuo:"请坐",qingjin:"请进",mafan:"麻烦",
    daodile:"到底了",zhendeshi:"真的是",name:"那么",zheyang:"这样",
    xiaoxi:"消息",tongzhi:"通知",anpai:"安排",querenxia:"确认下",
    qingqueren:"请确认",fasongle:"发送了",shoushang:"收上",
    haikeyile:"还可以了",chachabuluo:"差不多了",
  };

  // ── 简拼索引（首字母缩写 → 词组列表）─────────────────────────────────
  // 例: "jintian" → 首字母 "jt"
  // 支持纯声母缩写（每个音节取第一个字母）
  const ABBR = {}; // abbr → [word1, word2, ...]

  // 从完整拼音中提取每个音节的首字母（声母缩写）
  // 策略：贪心地从左向右匹配已知音节，取每个音节第一字母
  const SYLLS = Object.keys(D).sort((a, b) => b.length - a.length);

  function extractAbbr(py) {
    let s = py, abbr = '';
    while (s.length) {
      let matched = false;
      for (const syl of SYLLS) {
        if (s.startsWith(syl)) {
          abbr += syl[0];
          s = s.slice(syl.length);
          matched = true;
          break;
        }
      }
      if (!matched) { abbr += s[0]; s = s.slice(1); }
    }
    return abbr;
  }

  // 构建简拼索引
  for (const [py, word] of Object.entries(W)) {
    const abbr = extractAbbr(py);
    if (abbr !== py) { // 仅当缩写与完整拼音不同时才建索引
      if (!ABBR[abbr]) ABBR[abbr] = [];
      ABBR[abbr].push(word);
    }
  }

  // ── 核心逻辑 ─────────────────────────────────────────────────────────────
  let cnMode      = false;  // 中文模式
  let preedit      = '';    // 正在输入的拼音串
  let preeditStart = -1;   // 拼音起始 selectionStart（在 textarea 中）
  let cands        = [];    // 当前候选
  let page         = 0;    // 当前页码（0-indexed）
  let _lastPreedit = '';   // 上次渲染的拼音，用于检测拼音变化
  let focused      = null;  // 当前 input/textarea

  // 将所有词组键排序（长的在前，优先匹配多音节词）
  const WKEYS = Object.keys(W).sort((a, b) => b.length - a.length);

  /**
   * 尝试将拼音串完整分解为已知音节序列（贪心最长匹配）。
   * 成功返回音节数组，无法完整分解返回 null。
   * 例：'jintian' → ['jin','tian']；'jt' → null；'jin' → ['jin']
   */
  function decomposesSylls(py) {
    let s = py, parts = [];
    while (s.length) {
      let matched = false;
      for (const syl of SYLLS) {   // SYLLS 按长度降序，优先长音节
        if (s.startsWith(syl)) {
          parts.push(syl);
          s = s.slice(syl.length);
          matched = true;
          break;
        }
      }
      if (!matched) return null;   // 剩余部分不是合法音节 → 简拼/缩写
    }
    return parts;
  }

  function getCandidates(py) {
    if (!py) return [];
    const seen = new Set();
    const res  = [];
    function add(v) { if (!seen.has(v)) { seen.add(v); res.push(v); } }

    const sylls           = decomposesSylls(py);
    const isSingleSyllable = sylls !== null && sylls.length === 1;
    // 多音节全拼（jintian）或简拼缩写（jt）均走"词组优先"分支
    const wordsFirst       = sylls === null || sylls.length > 1;

    if (isSingleSyllable) {
      // ── 完整单音节 → 单字优先，词组其次 ─────────────────────
      if (D[py]) D[py].split('').forEach(add);
      for (const k of WKEYS) {
        if (res.length >= 50) break;
        if (k.startsWith(py)) add(W[k]);
      }
      for (const [abbr, words] of Object.entries(ABBR)) {
        if (abbr.startsWith(py[0])) words.forEach(add);
        if (res.length >= 50) break;
      }
    } else {
      // ── 简拼缩写 OR 多音节全拼 → 词组优先 ───────────────────
      // 1. 精确匹配：全拼词组 + 简拼
      if (W[py])    add(W[py]);
      if (ABBR[py]) ABBR[py].forEach(add);
      // 2. 前缀匹配：全拼前缀词组
      for (const k of WKEYS) {
        if (res.length >= 50) break;
        if (k !== py && k.startsWith(py)) add(W[k]);
      }
      // 3. 前缀匹配：简拼前缀
      for (const [abbr, words] of Object.entries(ABBR)) {
        if (abbr !== py && abbr.startsWith(py)) words.forEach(add);
        if (res.length >= 50) break;
      }
      // 4. 多音节全拼：提取每音节首字组合作为候补（如 jin+tian → 今天、进天…）
      if (sylls && sylls.length > 1) {
        const charSets = sylls.map(s => D[s] ? D[s].split('') : []);
        if (charSets.every(cs => cs.length > 0)) {
          const tail = charSets.slice(1).map(cs => cs[0]).join('');
          charSets[0].slice(0, 5).forEach(c => add(c + tail));
        }
      }
      // 5. 首音节单字 fallback（简拼时兜底）
      if (res.length < 9) {
        let best = '';
        for (const syl of SYLLS) {
          if (py.startsWith(syl) && syl.length > best.length) best = syl;
        }
        if (best && D[best]) {
          D[best].split('').forEach(add);
        } else if (!best) {
          // 6. 模糊前缀（单字母时）
          for (const [syl, chars] of Object.entries(D)) {
            if (syl.startsWith(py)) chars.split('').slice(0, 3).forEach(add);
            if (res.length >= 50) break;
          }
        }
      }
    }

    return res;
  }

  function commit(idx) {
    const c = cands[idx];
    if (!c || !focused) return;
    // 将 textarea 中的拼音字母整体替换为汉字
    const curPos = focused.selectionStart;
    const start  = preeditStart !== -1 ? preeditStart : curPos - preedit.length;
    const before = focused.value.slice(0, start);
    const after  = focused.value.slice(curPos);
    focused.value = before + c + after;
    focused.setSelectionRange(before.length + c.length, before.length + c.length);
    focused.dispatchEvent(new Event('input', { bubbles: true }));
    preedit = '';
    preeditStart = -1;
    renderBar();
  }

  function insertAtCursor(text) {
    const el = focused;
    if (!el) return;
    const s = el.selectionStart, e = el.selectionEnd;
    el.value = el.value.slice(0, s) + text + el.value.slice(e);
    el.setSelectionRange(s + text.length, s + text.length);
    el.dispatchEvent(new Event('input', { bubbles: true }));
  }

  // ── UI ───────────────────────────────────────────────────────────────────
  let bar = null;

  function ensureBar() {
    if (bar) return;
    bar = document.createElement('div');
    bar.id = 'ime-cand-bar';
    bar.style.cssText = [
      'position:fixed','top:0','left:0',
      'background:#1e2430','border:1px solid #30363d','border-radius:8px',
      'padding:5px 10px','font-size:15px','color:#c9d1d9','z-index:2147483647',
      'display:none','flex-wrap:nowrap','gap:4px','align-items:center',
      'box-shadow:0 4px 20px rgba(0,0,0,.6)','user-select:none','white-space:nowrap'
    ].join(';');
    document.body.appendChild(bar);
  }

  // 基于 github.com/component/textarea-caret-position 的成熟方案
  // 关键：用 span.offsetTop/offsetLeft 而非 getBoundingClientRect()
  // 前者对 visibility:hidden 元素有效，后者在 QtWebEngine 里返回全零
  const _MIRROR_PROPS = [
    'boxSizing','width','height','overflowX','overflowY',
    'borderTopWidth','borderRightWidth','borderBottomWidth','borderLeftWidth','borderStyle',
    'paddingTop','paddingRight','paddingBottom','paddingLeft',
    'fontStyle','fontVariant','fontWeight','fontStretch','fontSize',
    'lineHeight','fontFamily','textAlign','textTransform',
    'textIndent','letterSpacing','wordSpacing','tabSize',
  ];

  function getCursorCoords(el) {
    const elRect = el.getBoundingClientRect();
    const cs     = window.getComputedStyle(el);

    const div = document.createElement('div');
    div.style.position   = 'absolute';   // absolute 而非 fixed，offsetTop/Left 才准确
    div.style.visibility = 'hidden';
    div.style.whiteSpace = 'pre-wrap';
    div.style.wordWrap   = 'break-word';
    _MIRROR_PROPS.forEach(p => { div.style[p] = cs[p]; });
    document.body.appendChild(div);

    // 光标前的文字
    div.textContent = el.value.substring(0, el.selectionStart);

    // 光标位置用 span 占位
    const span = document.createElement('span');
    span.textContent = el.value.substring(el.selectionStart) || '.';
    div.appendChild(span);

    // offsetTop/offsetLeft 在 visibility:hidden 下依然准确
    const coords = {
      x: elRect.left + span.offsetLeft + parseInt(cs.borderLeftWidth || 0) - el.scrollLeft,
      y: elRect.top  + span.offsetTop  + parseInt(cs.borderTopWidth  || 0) - el.scrollTop
         + parseFloat(cs.lineHeight || cs.fontSize || 20),
    };

    document.body.removeChild(div);
    return coords;
  }

  const PAGE_SIZE = 5;

  function renderBar() {
    ensureBar();
    if (preedit !== _lastPreedit) { page = 0; _lastPreedit = preedit; }
    cands = getCandidates(preedit);  // 本地字典立即响应，零延迟
    _renderCands();
    // 单音节全拼（jin/ni）本地字典已够用；简拼或多音节才查后端词组
    if (cnMode && preedit) {
      const sylls = decomposesSylls(preedit);
      if (sylls === null || sylls.length > 1) _fetchBackend(preedit);
    }
  }

  function _renderCands() {
    if (!cnMode || !preedit) {
      bar.style.display = 'none';
      return;
    }

    const start  = page * PAGE_SIZE;
    const slice  = cands.slice(start, start + PAGE_SIZE);
    const hasPrev = page > 0;
    const hasNext = cands.length > start + PAGE_SIZE;

    let html = '';
    if (hasPrev) {
      html += '<span class="ime-nav" data-dir="-1" style="cursor:pointer;padding:2px 8px;'
        + 'border-radius:4px;color:#58a6ff;font-weight:bold">-</span>'
        + '<span style="color:#30363d;margin:0 2px">|</span>';
    }
    slice.forEach((c, i) => {
      html += '<span class="ime-c" data-i="' + i + '" style="cursor:pointer;'
        + 'padding:3px 8px;border-radius:4px">'
        + '<span style="color:#8b949e;font-size:11px;margin-right:2px">'
        + (i + 1) + '</span>' + esc(c) + '</span>';
    });
    if (hasNext) {
      html += '<span style="color:#30363d;margin:0 2px">|</span>'
        + '<span class="ime-nav" data-dir="1" style="cursor:pointer;padding:2px 8px;'
        + 'border-radius:4px;color:#58a6ff;font-weight:bold">+</span>';
    }
    bar.innerHTML = html;
    bar.style.display = 'flex';

    // x 跟随光标横坐标（夹在输入框范围内防坐标异常），y 优先放下方、空间不足翻到上方
    if (focused) {
      const rect   = focused.getBoundingClientRect();
      const coords = getCursorCoords(focused);
      const anchorX = Math.max(rect.left, Math.min(coords.x, rect.right - 20));
      bar.style.left   = anchorX + 'px';
      bar.style.top    = (rect.bottom + 4) + 'px';
      bar.style.bottom = 'auto';
      // 双帧 RAF：第一帧触发布局，第二帧时 getBoundingClientRect 才能拿到真实宽度
      requestAnimationFrame(() => requestAnimationFrame(() => {
        const r  = bar.getBoundingClientRect();
        const vw = window.innerWidth;
        const vh = window.innerHeight;
        // 超出下边界 → 翻转到输入框上方
        if (r.bottom > vh - 4) {
          bar.style.top    = 'auto';
          bar.style.bottom = (vh - rect.top + 4) + 'px';
        }
        // 超出右边界 → 右对齐左扩展（而非溢出消失）
        if (r.right > vw - 8) {
          bar.style.left = Math.max(8, vw - r.width - 8) + 'px';
        }
      }));
    }

    bar.querySelectorAll('.ime-c').forEach(el => {
      el.addEventListener('mousedown', ev => {
        ev.preventDefault();
        commit(+el.dataset.i);
      });
    });
    bar.querySelectorAll('.ime-nav').forEach(el => {
      el.addEventListener('mousedown', ev => {
        ev.preventDefault();
        page = Math.max(0, page + (+el.dataset.dir));
        _renderCands();
      });
    });
  }

  // null=未探测, true=可用, false=不可用（避免重复请求失败的后端）
  let _backendAvail = null;

  async function _fetchBackend(py) {
    if (_backendAvail === false) return;
    try {
      const r = await fetch('/api/ime?q=' + encodeURIComponent(py) + '&limit=50', { cache: 'no-store' });
      if (!r.ok) { _backendAvail = false; return; }
      _backendAvail = true;
      const d = await r.json();
      if (preedit === py && d.cands && d.cands.length > 0) {
        cands = d.cands;
        _renderCands();
      }
    } catch (e) {
      _backendAvail = false;
    }
  }

  function esc(s) {
    return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  }

  // ── 键盘拦截（capture 阶段，最先执行）──────────────────────────────────
  document.addEventListener('keydown', function (e) {
    // Ctrl+Space：切换中/英
    if (e.key === ' ' && e.ctrlKey && !e.shiftKey && !e.altKey) {
      e.preventDefault();
      e.stopPropagation();
      // 切换时同步捕获焦点元素（Qt 首次点击不触发 focusin，需在此兜底）
      if (!focused) {
        const ae = document.activeElement;
        if (ae && (ae.tagName === 'TEXTAREA' || ae.tagName === 'INPUT')) focused = ae;
      }
      cnMode = !cnMode;
      if (!cnMode && preedit && focused && preeditStart !== -1) {
        const curPos = focused.selectionStart;
        focused.value = focused.value.slice(0, preeditStart) + focused.value.slice(curPos);
        focused.setSelectionRange(preeditStart, preeditStart);
        focused.dispatchEvent(new Event('input', { bubbles: true }));
        preeditStart = -1;
      }
      if (!cnMode) preedit = '';
      renderBar();
      return;
    }

    // activeElement 兜底：Qt 首次点击可能不触发 focusin
    if (cnMode && !focused) {
      const ae = document.activeElement;
      if (ae && (ae.tagName === 'TEXTAREA' || ae.tagName === 'INPUT')) focused = ae;
    }
    if (!cnMode || !focused) return;

    // 字母键 → 写入 textarea 并累积拼音
    if (/^[a-zA-Z]$/.test(e.key) && !e.ctrlKey && !e.altKey && !e.metaKey) {
      e.preventDefault();
      e.stopPropagation();
      if (preeditStart === -1) preeditStart = focused.selectionStart;
      insertAtCursor(e.key.toLowerCase());
      preedit += e.key.toLowerCase();
      renderBar();
      return;
    }

    if (!preedit) return;  // 以下仅在有待选内容时生效

    // Backspace → 删拼音末位（同步从 textarea 删除）
    if (e.key === 'Backspace') {
      e.preventDefault();
      e.stopPropagation();
      if (preedit.length > 1) {
        const s = focused.selectionStart;
        focused.value = focused.value.slice(0, s - 1) + focused.value.slice(s);
        focused.setSelectionRange(s - 1, s - 1);
        focused.dispatchEvent(new Event('input', { bubbles: true }));
        preedit = preedit.slice(0, -1);
      } else {
        const curPos = focused.selectionStart;
        focused.value = focused.value.slice(0, preeditStart) + focused.value.slice(curPos);
        focused.setSelectionRange(preeditStart, preeditStart);
        focused.dispatchEvent(new Event('input', { bubbles: true }));
        preedit = '';
        preeditStart = -1;
      }
      renderBar();
      return;
    }
    // +/= → 下一页；- → 上一页
    if ((e.key === '+' || e.key === '=') && !e.ctrlKey) {
      if (cands.length > (page + 1) * PAGE_SIZE) {
        e.preventDefault(); e.stopPropagation();
        page++; _renderCands();
      }
      return;
    }
    if (e.key === '-' && !e.ctrlKey) {
      if (page > 0) {
        e.preventDefault(); e.stopPropagation();
        page--; _renderCands();
      }
      return;
    }
    // Space → 上屏第一候选
    if (e.key === ' ' && !e.ctrlKey) {
      e.preventDefault();
      e.stopPropagation();
      if (cands[0]) commit(0);
      else { preedit = ''; preeditStart = -1; renderBar(); }
      return;
    }
    // 数字 1-5 → 选当前页第 n 个候选
    if (/^[1-5]$/.test(e.key) && !e.ctrlKey) {
      const i = +e.key - 1;
      const actual = page * PAGE_SIZE + i;
      if (cands[actual]) {
        e.preventDefault();
        e.stopPropagation();
        const saved = cands[actual];
        cands = [saved]; // commit(0) 用
        page = 0;
        commit(0);
      }
      return;
    }
    // Enter → 上屏第一候选
    if (e.key === 'Enter') {
      e.preventDefault();
      e.stopPropagation();
      if (cands[0]) commit(0);
      else { preedit = ''; preeditStart = -1; renderBar(); }
      return;
    }
    // Escape → 取消（从 textarea 中删除已输入的拼音）
    if (e.key === 'Escape') {
      e.preventDefault();
      e.stopPropagation();
      if (preedit && preeditStart !== -1) {
        const curPos = focused.selectionStart;
        focused.value = focused.value.slice(0, preeditStart) + focused.value.slice(curPos);
        focused.setSelectionRange(preeditStart, preeditStart);
        focused.dispatchEvent(new Event('input', { bubbles: true }));
      }
      preedit = '';
      preeditStart = -1;
      renderBar();
      return;
    }
    // 其他键（标点等）→ 先上屏第一候选，再直接输入
    if (cands[0]) commit(0);
  }, true /* capture */);

  // ── 焦点追踪 ─────────────────────────────────────────────────────────────
  // mousedown 补充：Qt 首次点击激活窗口时 focusin 不触发，mousedown 可捕获到
  document.addEventListener('mousedown', e => {
    if (e.target.tagName === 'TEXTAREA' || e.target.tagName === 'INPUT') {
      focused = e.target;
    }
  }, true);
  document.addEventListener('focusin', e => {
    if (e.target.tagName === 'TEXTAREA' || e.target.tagName === 'INPUT') {
      focused = e.target;
    }
  });
  document.addEventListener('focusout', e => {
    if (e.target === focused) {
      if (preedit && preeditStart !== -1) {
        const curPos = focused.selectionStart;
        focused.value = focused.value.slice(0, preeditStart) + focused.value.slice(curPos);
        focused.setSelectionRange(preeditStart, preeditStart);
        focused.dispatchEvent(new Event('input', { bubbles: true }));
      }
      preedit = '';
      preeditStart = -1;
      renderBar();
    }
  });

  // ── 初始化 ───────────────────────────────────────────────────────────────
  setTimeout(() => { ensureBar(); }, 800);
  console.log('[IME] JS 拼音输入法已加载。Ctrl+Space 切换中/英文。');
})();
